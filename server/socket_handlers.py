from __future__ import annotations

import logging
from typing import Any, Dict

from server import ai
from server.config import Settings
from server.rtc import RTCManager
from server.state import AppState
from server.utils import now_ms, parse_connect_query


log = logging.getLogger("server.socket_handlers")



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

        if role == "watch" and room.broadcaster_sid is None:
            await sio.emit("waiting_for_broadcaster", {"room": room_id, "ts": now_ms()}, to=sid)

    @sio.event
    async def disconnect(sid):
        room_id, role = state.pop_sid_meta(sid)
        log.info("disconnect sid=%s room=%s role=%s", sid, room_id, role)

        room = state.ensure_room(room_id)
        if room.broadcaster_sid == sid:
            await rtc.stop_broadcaster(room_id, sid)
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

    @sio.on("speak_last_ai")
    async def speak_last_ai(sid, data):
        room_id, _ = state.get_sid_meta(sid)
        text = ((data or {}).get("text") or "").strip()
        if not text:
            return
        # compatibility: emit URL event consumers expect
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

        payload = data or {}
        b64_audio = payload.get("b64") or payload.get("content_base64") or ""
        if not b64_audio:
            return

        try:
            import base64

            audio_bytes = base64.b64decode(b64_audio)
        except Exception:
            log.warning("invalid stt_chunk payload room=%s sid=%s", room_id, sid)
            return

        text = await ai.transcribe_track([audio_bytes], mime=(payload.get("mime") or "audio/webm"))
        if not text:
            return

        await sio.emit(
            "stt_text",
            {"text": text, "ts": now_ms()},
            to=f"room:{room_id}:all",
        )

    @sio.on("webrtc_offer")
    async def webrtc_offer(sid, data):
        room_id, _role = state.get_sid_meta(sid)
        sdp = (data or {}).get("sdp")
        sdp_type = (data or {}).get("type")
        if not sdp or not sdp_type:
            return
        answer = await rtc.start_broadcaster_from_offer(room_id, sid, sdp, sdp_type)
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
