from __future__ import annotations

import base64
import logging
from typing import Any

from server import ai
from server.config import Settings
from server.rtc import RTCManager
from server.state import AppState
from server.utils import now_ms, parse_connect_query


log = logging.getLogger("server.socket_handlers")


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


async def _emit_room_status(sio, room_id: str, room) -> None:
    await sio.emit(
        "room_status",
        {
            "room": room_id,
            "viewer_count": len(room.viewers),
            "broadcaster_present": room.broadcaster_sid is not None,
            "ai": {"enabled": room.settings.ai_enabled, "power": True, "mode": "active", "fallback": True},
            "stt_enabled": room.settings.stt_enabled,
            "tts_enabled": room.settings.tts_enabled,
            "ts": now_ms(),
        },
        to=f"room:{room_id}:all",
    )


async def _emit_stage_state(sio, room_id: str, room) -> None:
    await sio.emit("stage_state", _stage_payload(room_id, room), to=f"room:{room_id}:all")


def register_socket_handlers(sio, state: AppState, settings: Settings, rtc: RTCManager) -> None:
    @sio.event
    async def connect(sid, environ, auth):
        room_id, role = parse_connect_query(environ, settings.default_room, settings.max_room_len)
        state.set_sid_meta(sid, room_id, role)
        room = state.ensure_room(room_id)

        await sio.enter_room(sid, f"room:{room_id}:all")
        if role == "broadcast":
            await sio.enter_room(sid, f"room:{room_id}:broadcast")
        elif role == "watch":
            await sio.enter_room(sid, f"room:{room_id}:watch")

        log.info("connect sid=%s room=%s role=%s", sid, room_id, role)
        await _emit_room_status(sio, room_id, room)
        await _emit_stage_state(sio, room_id, room)

        if role == "watch" and room.broadcaster_sid is None:
            await sio.emit("waiting_for_broadcaster", {"room": room_id, "ts": now_ms()}, to=sid)

    @sio.event
    async def disconnect(sid):
        room_id, role = state.pop_sid_meta(sid)
        log.info("disconnect sid=%s room=%s role=%s", sid, room_id, role)

        room = state.ensure_room(room_id)
        if room.broadcaster_sid == sid:
            await rtc.stop_broadcaster(room_id, sid)
            room.media.live_active = False
            room.media.mode = "upload" if room.media.latest_upload_url else "none"
            await _emit_stage_state(sio, room_id, room)
        else:
            await rtc.stop_viewer(room_id, sid)

    @sio.on("set_room_settings")
    async def set_room_settings(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        room = state.ensure_room(room_id)
        payload = data or {}
        if "ai_enabled" in payload:
            room.settings.ai_enabled = bool(payload.get("ai_enabled"))
        if "tts_enabled" in payload:
            room.settings.tts_enabled = bool(payload.get("tts_enabled"))
        if "stt_enabled" in payload:
            room.settings.stt_enabled = bool(payload.get("stt_enabled"))
        await _emit_room_status(sio, room_id, room)

    @sio.on("stt_toggle")
    async def stt_toggle(sid, data):
        room_id, role = state.get_sid_meta(sid)
        if role != "broadcast":
            return
        room = state.ensure_room(room_id)
        room.settings.stt_enabled = bool((data or {}).get("enabled"))
        await _emit_room_status(sio, room_id, room)

    @sio.on("chat_message")
    async def chat_message(sid, data):
        room_id, role = state.get_sid_meta(sid)
        text = ((data or {}).get("text") or "").strip()
        if not text:
            return
        sender = "broadcaster" if role == "broadcast" else "user"
        await sio.emit(
            "chat_message",
            {
                "sender": sender,
                "role": "user",
                "text": text,
                "ts": now_ms(),
            },
            to=f"room:{room_id}:all",
        )

    @sio.on("upload_file")
    async def upload_file(sid, data):
        room_id, role = state.get_sid_meta(sid)
        room = state.ensure_room(room_id)
        payload = data or {}
        content_b64 = payload.get("content_base64") or ""
        if not content_b64:
            return

        upload_type = (payload.get("upload_type") or "location_video").strip()
        mime = (payload.get("mime") or "application/octet-stream").strip()
        location_id = (payload.get("locationId") or payload.get("location_id") or room_id).strip()

        try:
            base64.b64decode(content_b64)
        except Exception:
            await sio.emit("stt_error", {"message": "Invalid upload payload", "ts": now_ms()}, to=sid)
            return

        fake_url = f"data:{mime};base64,{content_b64}"
        room.latest_upload = {
            "url": fake_url,
            "timestamp": now_ms(),
            "locationId": location_id,
            "mime": mime,
            "upload_type": upload_type,
            "name": payload.get("name") or "upload",
            "sid": sid,
        }

        if upload_type == "location_video" and role == "broadcast":
            room.media.latest_upload_url = fake_url
            room.media.latest_upload_mime = mime
            room.media.latest_upload_at = room.latest_upload["timestamp"]
            room.media.location_id = location_id
            if not room.media.live_active:
                room.media.mode = "upload"
            await _emit_stage_state(sio, room_id, room)

        await sio.emit(
            "chat_message",
            {
                "sender": "broadcaster" if role == "broadcast" else "user",
                "role": "attachment",
                "text": payload.get("text") or f"uploaded {payload.get('name') or 'file'}",
                "upload_type": upload_type,
                "url": fake_url,
                "mime": mime,
                "locationId": location_id,
                "ts": now_ms(),
            },
            to=f"room:{room_id}:all",
        )

    @sio.on("speak_last_ai")
    async def speak_last_ai(sid, data):
        room_id, _ = state.get_sid_meta(sid)
        text = ((data or {}).get("text") or "").strip()
        if not text:
            return
        await sio.emit(
            "ai_tts_audio",
            {"room": room_id, "url": "", "mime": "audio/wav", "ts": now_ms()},
            to=f"room:{room_id}:all",
        )

    @sio.on("stt_chunk")
    async def stt_chunk(sid, data):
        room_id, role = state.get_sid_meta(sid)
        if role != "broadcast":
            return

        room = state.ensure_room(room_id)
        if not room.settings.stt_enabled:
            return

        payload = data or {}
        b64_audio = payload.get("b64") or payload.get("content_base64") or ""
        if not b64_audio:
            return

        try:
            audio_bytes = base64.b64decode(b64_audio)
        except Exception:
            await sio.emit("stt_error", {"message": "Invalid STT chunk", "ts": now_ms()}, to=sid)
            return

        await sio.emit("transcript_partial", {"text": "…", "ts": now_ms()}, to=sid)

        text = await ai.transcribe_track([audio_bytes], mime=(payload.get("mime") or "audio/webm"))
        if not text:
            return

        final_payload = {"text": text, "ts": now_ms()}
        await sio.emit("transcript_final", final_payload, to=f"room:{room_id}:all")
        await sio.emit("stt_text", final_payload, to=f"room:{room_id}:all")

    @sio.on("webrtc_offer")
    async def webrtc_offer(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        room = state.ensure_room(room_id)
        sdp = (data or {}).get("sdp")
        sdp_type = (data or {}).get("type")
        if not sdp or not sdp_type:
            return
        answer = await rtc.start_broadcaster_from_offer(room_id, sid, sdp, sdp_type)
        room.media.live_active = True
        room.media.mode = "live"
        await _emit_stage_state(sio, room_id, room)
        await sio.emit("webrtc_answer", answer, to=sid)

    @sio.on("webrtc_ice")
    async def webrtc_ice(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        cand = rtc.parse_ice(data or {})
        await rtc.add_broadcaster_ice_candidate(room_id, sid, cand)

    @sio.on("watch_join")
    async def watch_join(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        offer = await rtc.start_viewer_offer(room_id, sid)
        await sio.emit("watch_offer", offer, to=sid)
        room = state.ensure_room(room_id)
        await _emit_stage_state(sio, room_id, room)
        if room.broadcaster_sid is None:
            await sio.emit("waiting_for_broadcaster", {"room": room_id, "ts": now_ms()}, to=sid)

    @sio.on("watch_answer")
    async def watch_answer(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        sdp = (data or {}).get("sdp")
        sdp_type = (data or {}).get("type")
        if not sdp or not sdp_type:
            return
        await rtc.set_viewer_answer(room_id, sid, sdp, sdp_type)

    @sio.on("watch_ice")
    async def watch_ice(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        cand = rtc.parse_ice(data or {})
        await rtc.add_viewer_ice_candidate(room_id, sid, cand)
