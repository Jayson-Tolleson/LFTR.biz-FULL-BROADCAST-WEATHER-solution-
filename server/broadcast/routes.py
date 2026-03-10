from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from quart import jsonify, request, websocket

from server import ai
from server.ai.gemini import generate_ai_reply, stream_ai_reply
from server.ai.speech import synthesize_voice
from server.media.upload import save_upload
from server.state import AppState
from server.utils import now_ms


log = logging.getLogger("server.broadcast.routes")
STT_SOURCE_LITERAL = {"source": "stt"}


class RoomRegistry:
    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, dict[Any, str]]] = defaultdict(lambda: {"chat": {}, "broadcast": {}, "watch": {}})

    def register(self, room: str, kind: str, ws: Any, client_id: str) -> None:
        self.rooms[room][kind][ws] = client_id

    def unregister(self, room: str, kind: str, ws: Any) -> str | None:
        bucket = self.rooms.get(room, {}).get(kind, {})
        cid = bucket.pop(ws, None)
        if room in self.rooms and not any(self.rooms[room][k] for k in ("chat", "broadcast", "watch")):
            self.rooms.pop(room, None)
        return cid

    def viewer_count(self, room: str) -> int:
        return len(self.rooms.get(room, {}).get("watch", {}))

    async def send_ws(self, ws: Any, message: dict[str, Any]) -> None:
        await ws.send_json(message)

    async def broadcast_room(self, room: str, message: dict[str, Any], kinds: tuple[str, ...] = ("chat", "broadcast", "watch")) -> None:
        dead: list[tuple[str, Any]] = []
        room_obj = self.rooms.get(room, {})
        for kind in kinds:
            for ws in list(room_obj.get(kind, {}).keys()):
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append((kind, ws))
        for kind, ws in dead:
            self.unregister(room, kind, ws)


registry = RoomRegistry()


async def _set_ai_status(state: AppState, room_id: str, status: str) -> None:
    room = state.ensure_room(room_id)
    room.settings.ai_status = "active" if status == "active" else "idle"
    await registry.broadcast_room(room_id, {"type": "ai_status", "room": room_id, "status": room.settings.ai_status, "ts": now_ms()})


def _normalize_room_client(payload: dict[str, Any], default_room: str, role: str) -> tuple[str, str]:
    room = str(payload.get("room") or payload.get("payload", {}).get("room") or default_room or "default")
    client_id = str(payload.get("clientId") or payload.get("payload", {}).get("clientId") or f"{role}:{id(payload)}")
    return room, client_id


def _stage_payload(room_id: str, room) -> dict[str, Any]:
    media = room.media
    mode = "live" if media.live_active else ("upload" if media.latest_upload_url else "none")
    return {
        "room": room_id,
        "mode": mode,
        "liveActive": bool(media.live_active),
        "latestUploadUrl": media.latest_upload_url,
        "latestUploadMime": media.latest_upload_mime,
        "latestUploadAt": media.latest_upload_at,
        "locationId": media.location_id,
        "label": media.label,
        "ts": now_ms(),
    }


async def _broadcast_presence(state: AppState, room_id: str) -> None:
    room = state.ensure_room(room_id)
    room.runtime.viewer_count = registry.viewer_count(room_id)
    room.runtime.broadcaster_present = room.broadcaster_sid is not None
    room.runtime.broadcast_connected = room.broadcaster_sid is not None
    room.runtime.chat_connected = bool(registry.rooms.get(room_id, {}).get("chat"))
    room.runtime.watch_connected = room.runtime.viewer_count > 0

    await registry.broadcast_room(
        room_id,
        {
            "type": "presence",
            "room": room_id,
            "broadcaster_present": room.runtime.broadcaster_present,
            "viewer_count": room.runtime.viewer_count,
            "ts": now_ms(),
        },
    )
    await registry.broadcast_room(room_id, {"type": "viewer_count", "room": room_id, "count": room.runtime.viewer_count, "ts": now_ms()})
    snapshot = state.room_state_payload(room_id)
    await registry.broadcast_room(room_id, {"type": "state_sync", "room": room_id, "state": snapshot, "ts": now_ms()})
    await registry.broadcast_room(room_id, {"type": "state_update", "room": room_id, "state": snapshot, "ts": now_ms()})


async def _chat_emit(room_id: str, payload: dict[str, Any]) -> None:
    await registry.broadcast_room(room_id, {"type": "chat", "room": room_id, **payload, "ts": now_ms()})


async def _handle_chat_text(state: AppState, room_id: str, client_id: str, role: str, text: str, source: str | None = None) -> None:
    room = state.ensure_room(room_id)
    base_payload = {"user": role, "clientId": client_id, "text": text}
    if source:
        base_payload["source"] = source
    await _chat_emit(room_id, base_payload)
    if not room.settings.ai_enabled:
        return
    await _set_ai_status(state, room_id, "active")
    try:
        ai_text = ""
        async for token in stream_ai_reply(text):
            ai_text += token
            await registry.broadcast_room(room_id, {"type": "ai_partial", "room": room_id, "user": "ai", "text": ai_text, "ts": now_ms()})
        final_text = ai_text or generate_ai_reply(text)["text"]
        payload = {"type": "ai", "room": room_id, "user": "ai", "text": final_text, "ts": now_ms()}
        if room.settings.hear_ai_voice and room.settings.tts_enabled:
            payload["voice"] = synthesize_voice(final_text)
        await registry.broadcast_room(room_id, payload)
    except Exception as exc:
        log.exception("ai chat generation failed")
        await registry.broadcast_room(room_id, {"type": "chat", "room": room_id, "user": "assistant", "text": f"AI unavailable: {exc}", "ts": now_ms()})
    finally:
        await _set_ai_status(state, room_id, "idle")


def _merge_room_state(room, update: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "ai_enabled": bool(update.get("ai_enabled", room.settings.ai_enabled)),
        "stt_enabled": bool(update.get("stt_enabled", room.settings.stt_enabled)),
        "tts_enabled": bool(update.get("tts_enabled", room.settings.tts_enabled)),
        "hear_ai_voice": bool(update.get("hear_ai_voice", room.settings.hear_ai_voice)),
        "mic_enabled": bool(update.get("mic_enabled", room.settings.mic_enabled)),
        "camera_enabled": bool(update.get("camera_enabled", room.settings.camera_enabled)),
        "screen_enabled": bool(update.get("screen_enabled", room.settings.screen_enabled)),
        "noise_cancel_enabled": bool(update.get("noise_cancel_enabled", room.settings.noise_cancel_enabled)),
    }
    for k, v in normalized.items():
        setattr(room.settings, k, v)
    return normalized


def register_broadcast_routes(app, state: AppState | None = None, rtc=None) -> None:
    state = state or AppState(default_room="default")

    async def _emit_room_from_rtc(room_id: str, message: dict[str, Any]) -> None:
        await registry.broadcast_room(room_id, {"room": room_id, **message})

    state.ws_emit_room = _emit_room_from_rtc

    @app.websocket('/ws/chat')
    async def ws_chat():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"chat:{id(ws)}"
        role = "participant"
        joined = False
        log.info("chat socket connected room=%s client=%s", room_id, client_id)
        try:
            while True:
                try:
                    payload = await websocket.receive_json()
                except Exception as exc:
                    log.info("/ws/chat receive closed room=%s client=%s reason=%s", room_id, client_id, exc.__class__.__name__)
                    break
                kind = payload.get("type", "chat")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                if kind == "join":
                    room_id, client_id = _normalize_room_client(data, state.default_room, "chat")
                    role = str(data.get("role") or "participant")
                    registry.register(room_id, "chat", ws, client_id)
                    joined = True
                    await ws.send_json({"type": "connected", "room": room_id, "clientId": client_id, "role": role, "ts": now_ms()})
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await ws.send_json({"type": "stage_state", "payload": _stage_payload(room_id, state.ensure_room(room_id))})
                    await _broadcast_presence(state, room_id)
                    continue

                if not joined:
                    room_id, client_id = _normalize_room_client(data, state.default_room, "chat")
                    registry.register(room_id, "chat", ws, client_id)
                    joined = True

                room = state.ensure_room(room_id)
                if kind == "ping":
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == "chat":
                    text = str(data.get("text") or "").strip()
                    if text:
                        await _handle_chat_text(state, room_id, client_id, role, text)
                elif kind == "toggle_state":
                    _merge_room_state(room, data.get("state") or {})
                    await _broadcast_presence(state, room_id)
                elif kind == "web_search":
                    query = str(data.get("query") or "").strip()
                    if query and room.settings.web_search_enabled:
                        res = await ai.handle_websearch({"query": query})
                        await registry.broadcast_room(room_id, {"type": "web_search_result", "room": room_id, "query": query, "result": res, "ts": now_ms()})
                elif kind == "attachment_uploaded":
                    attachment = data.get("attachment") or {}
                    await registry.broadcast_room(room_id, {"type": "attachment", "room": room_id, "user": role, "clientId": client_id, "attachment": attachment, "ts": now_ms()})
                elif kind == "audio_chunk":
                    if not room.settings.stt_enabled:
                        continue
                    b64_data = str(data.get("data") or "")
                    if len(b64_data) > 2_000_000:
                        await ws.send_json({"type": "error", "room": room_id, "message": "audio_chunk_too_large", "ts": now_ms()})
                        continue
                    mime = str(data.get("mime") or "audio/webm")
                    try:
                        chunk = base64.b64decode(b64_data, validate=True)
                    except (ValueError, binascii.Error):
                        log.warning("invalid audio_chunk payload room=%s client=%s", room_id, client_id)
                        chunk = b""
                    except Exception:
                        log.exception("audio chunk decode failed room=%s client=%s", room_id, client_id)
                        chunk = b""

                    transcribe_fn = getattr(ai, "transcribe_track", None)
                    if not callable(transcribe_fn):
                        log.warning("transcribe_track missing room=%s client=%s", room_id, client_id)
                        continue
                    text = ""
                    if chunk:
                        try:
                            text = str(await transcribe_fn([chunk], mime=mime) or "").strip()
                        except Exception:
                            log.exception("stt failed room=%s client=%s", room_id, client_id)
                            text = ""
                    if text:
                        await _handle_chat_text(state, room_id, client_id, role, text, source="stt")
        finally:
            if joined:
                registry.unregister(room_id, "chat", ws)
                await _broadcast_presence(state, room_id)

    @app.websocket('/ws/broadcast')
    async def ws_broadcast():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"broadcaster:{id(ws)}"
        joined = False
        log.info("broadcast socket connected room=%s client=%s", room_id, client_id)
        try:
            while True:
                try:
                    payload = await websocket.receive_json()
                except Exception as exc:
                    log.info("/ws/broadcast receive closed room=%s client=%s reason=%s", room_id, client_id, exc.__class__.__name__)
                    break
                kind = payload.get("type", "ping")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                if kind == "join":
                    room_id, client_id = _normalize_room_client(data, state.default_room, "broadcaster")
                    registry.register(room_id, "broadcast", ws, client_id)
                    joined = True
                    room = state.ensure_room(room_id)
                    room.broadcaster_sid = client_id
                    room.runtime.broadcast_connected = True
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await ws.send_json({"type": "connectivity", "room": room_id, "connected": True, "ts": now_ms()})
                    await ws.send_json({"type": "stage_state", "payload": _stage_payload(room_id, room)})
                    await _broadcast_presence(state, room_id)
                    continue

                if not joined:
                    room_id, client_id = _normalize_room_client(data, state.default_room, "broadcaster")
                    registry.register(room_id, "broadcast", ws, client_id)
                    joined = True
                    room = state.ensure_room(room_id)
                    room.broadcaster_sid = client_id

                room = state.ensure_room(room_id)
                if kind == "ping":
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == "webrtc_offer":
                    sdp = data.get("sdp")
                    sdp_type = data.get("type") or "offer"
                    if sdp:
                        answer = await rtc.start_broadcaster_from_offer(room_id, client_id, sdp, sdp_type) if rtc is not None else {"sdp": None, "type": "answer"}
                        room.media.live_active = True
                        room.media.mode = "live"
                        await ws.send_json({"type": "webrtc_answer", "room": room_id, "clientId": client_id, "sdp": answer.get("sdp"), "answerType": answer.get("type", "answer"), "ts": now_ms()})
                        await registry.broadcast_room(room_id, {"type": "stage_state", "payload": _stage_payload(room_id, room)})
                        await _broadcast_presence(state, room_id)
                elif kind in {"webrtc_ice", "watch_ice"}:
                    if rtc is not None:
                        cand = rtc.parse_ice(data or {})
                        await rtc.add_broadcaster_ice_candidate(room_id, client_id, cand)
                elif kind == "toggle_state":
                    _merge_room_state(room, data.get("state") or {})
                    await _broadcast_presence(state, room_id)
                elif kind == "set_media_mode":
                    room.settings.camera_enabled = bool(data.get("camera", room.settings.camera_enabled))
                    room.settings.screen_enabled = bool(data.get("screen", room.settings.screen_enabled))
                    room.settings.mic_enabled = bool(data.get("mic", room.settings.mic_enabled))
                    await _broadcast_presence(state, room_id)
                elif kind == "chat":
                    text = str(data.get("text") or "").strip()
                    if text:
                        await _handle_chat_text(state, room_id, client_id, "broadcaster", text)
                elif kind == "attachment":
                    await registry.broadcast_room(room_id, {"type": "attachment", "room": room_id, "user": "broadcaster", "clientId": client_id, "attachment": data.get("attachment") or {}, "ts": now_ms()})
        finally:
            if joined:
                registry.unregister(room_id, "broadcast", ws)
                room = state.ensure_room(room_id)
                if room.broadcaster_sid == client_id:
                    room.broadcaster_sid = None
                if rtc is not None:
                    try:
                        await rtc.stop_broadcaster(room_id, client_id)
                    except Exception:
                        log.exception("broadcaster cleanup failed")
                await _broadcast_presence(state, room_id)

    @app.websocket('/ws/watch')
    async def ws_watch():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"watch:{id(ws)}"
        joined = False
        log.info("watch socket connected room=%s client=%s", room_id, client_id)
        try:
            while True:
                try:
                    payload = await websocket.receive_json()
                except Exception as exc:
                    log.info("/ws/watch receive closed room=%s client=%s reason=%s", room_id, client_id, exc.__class__.__name__)
                    break
                kind = payload.get("type", "join")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                if kind in {"join", "watch_join"}:
                    room_id, client_id = _normalize_room_client(data, state.default_room, "viewer")
                    log.info("watch join received room=%s client=%s", room_id, client_id)
                    registry.register(room_id, "watch", ws, client_id)
                    joined = True
                    state.ensure_room(room_id).viewers[client_id] = True
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await _broadcast_presence(state, room_id)
                    room = state.ensure_room(room_id)
                    if room.broadcaster_sid is None:
                        log.info("watch waiting room=%s client=%s no broadcaster", room_id, client_id)
                        await ws.send_json({"type": "presence", "room": room_id, "broadcaster_present": False, "viewer_count": len(room.viewers), "ts": now_ms()})
                        await ws.send_json({"type": "error", "room": room_id, "message": "no_broadcaster", "ts": now_ms()})
                    else:
                        log.info("watch signaling started room=%s client=%s", room_id, client_id)
                        if rtc is None:
                            await ws.send_json({"type": "error", "room": room_id, "message": "rtc_unavailable", "ts": now_ms()})
                        else:
                            offer = await rtc.start_viewer_offer(room_id, client_id)
                            await ws.send_json({"type": "watch_offer", "room": room_id, "payload": offer, "ts": now_ms()})
                    await ws.send_json({"type": "stage_state", "payload": _stage_payload(room_id, state.ensure_room(room_id))})
                    continue

                if kind == "ping":
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == "request_stream":
                    log.info("watch request_stream room=%s client=%s", room_id, client_id)
                    room = state.ensure_room(room_id)
                    if room.broadcaster_sid is None:
                        log.info("watch waiting room=%s client=%s no broadcaster", room_id, client_id)
                        await ws.send_json({"type": "presence", "room": room_id, "broadcaster_present": False, "viewer_count": len(room.viewers), "ts": now_ms()})
                        await ws.send_json({"type": "error", "room": room_id, "message": "no_broadcaster", "ts": now_ms()})
                        continue
                    if rtc is None:
                        await ws.send_json({"type": "error", "room": room_id, "message": "rtc_unavailable", "ts": now_ms()})
                    else:
                        offer = await rtc.start_viewer_offer(room_id, client_id)
                        await ws.send_json({"type": "watch_offer", "room": room_id, "payload": offer, "ts": now_ms()})
                elif kind in {"watch_answer", "webrtc_answer"}:
                    sdp = data.get("sdp")
                    sdp_type = data.get("type") or "answer"
                    if sdp:
                        if rtc is not None:
                            await rtc.set_viewer_answer(room_id, client_id, sdp, sdp_type)
                elif kind in {"webrtc_ice", "watch_ice"}:
                    if rtc is not None:
                        cand = rtc.parse_ice(data or {})
                        await rtc.add_viewer_ice_candidate(room_id, client_id, cand)
                elif kind == "chat":
                    text = str(data.get("text") or "").strip()
                    if text:
                        await _handle_chat_text(state, room_id, client_id, "viewer", text)
        finally:
            if joined:
                registry.unregister(room_id, "watch", ws)
                state.ensure_room(room_id).viewers.pop(client_id, None)
                if rtc is not None:
                    try:
                        await rtc.stop_viewer(room_id, client_id)
                    except Exception:
                        log.exception("watch cleanup failed")
                await _broadcast_presence(state, room_id)
            log.info("watch socket disconnected room=%s client=%s", room_id, client_id)

    @app.post('/api/upload')
    async def api_upload():
        files = await request.files
        file_storage = files.get("file")
        if not file_storage:
            return jsonify({"error": "file required"}), 400
        return jsonify(await save_upload(file_storage))
