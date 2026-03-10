from __future__ import annotations

from quart import jsonify, request, websocket
import logging

from server.ai.gemini import generate_ai_reply, stream_ai_reply
from server.ai.speech import synthesize_voice, transcribe_audio_chunk
from server.broadcast.websocket import hub
from server.media.upload import save_upload


log = logging.getLogger("server.broadcast.routes")

def register_broadcast_routes(app, state=None, rtc=None) -> None:
    @app.websocket('/ws/chat')
    async def ws_chat():
        ws = websocket._get_current_object()
        hub.chat_clients.add(ws)
        try:
            while True:
                payload = await websocket.receive_json()
                kind = payload.get("type", "chat")
                if kind == "audio":
                    text = transcribe_audio_chunk(payload.get("audio", ""))
                    if text:
                        await hub.publish_chat({"user": payload.get("user", "broadcaster"), "text": text, "type": "transcript"})
                        ai_text = ""
                        async for token in stream_ai_reply(text):
                            ai_text += token
                            await hub.publish_chat({"user": "ai", "text": ai_text, "type": "ai_partial"})
                        final_text = ai_text or generate_ai_reply(text)["text"]
                        await hub.publish_chat({"user": "ai", "text": final_text, "voice": synthesize_voice(final_text), "type": "ai"})
                    continue

                user = payload.get("user", "viewer")
                text = payload.get("text", "")
                outgoing = {"user": user, "text": text, "type": "chat"}
                await hub.publish_chat(outgoing)
                ai_text = ""
                async for token in stream_ai_reply(text):
                    ai_text += token
                    await hub.publish_chat({"user": "ai", "text": ai_text, "type": "ai_partial"})
                final_text = ai_text or generate_ai_reply(text)["text"]
                await hub.publish_chat({"user": "ai", "text": final_text, "voice": synthesize_voice(final_text), "type": "ai"})
        finally:
            hub.chat_clients.discard(ws)


    @app.websocket('/ws/watch')
    async def ws_watch():
        ws = websocket._get_current_object()
        sid = f"watch:{id(ws)}"
        room_id = "default"
        try:
            while True:
                payload = await websocket.receive_json()
                kind = payload.get("type", "watch_join")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                room_id = str((data or {}).get("room") or room_id or "default")
                if rtc is None:
                    await ws.send_json({"type": "webrtc_error", "payload": {"message": "watch rtc unavailable"}})
                    continue
                if kind == "watch_join":
                    try:
                        offer = await rtc.start_viewer_offer(room_id, sid)
                        await ws.send_json({"type": "watch_offer", "payload": offer})
                        if state is not None:
                            room = state.ensure_room(room_id)
                            await ws.send_json({"type": "room_status", "payload": {"ai": {"enabled": room.settings.ai_enabled}}})
                            await ws.send_json({"type": "stage_state", "payload": {"label": room.media.label, "mode": room.media.mode, "latestUploadUrl": room.media.latest_upload_url}})
                    except Exception as exc:
                        log.exception("watch_join failed room=%s sid=%s", room_id, sid)
                        await ws.send_json({"type": "webrtc_error", "payload": {"stage": "watch_join", "message": str(exc)}})
                elif kind == "watch_answer":
                    sdp = (data or {}).get("sdp")
                    sdp_type = (data or {}).get("type")
                    if not sdp or not sdp_type:
                        await ws.send_json({"type": "webrtc_error", "payload": {"stage": "watch_answer", "message": "missing_sdp"}})
                        continue
                    try:
                        await rtc.set_viewer_answer(room_id, sid, sdp, sdp_type)
                    except Exception as exc:
                        log.exception("watch_answer failed room=%s sid=%s", room_id, sid)
                        await ws.send_json({"type": "webrtc_error", "payload": {"stage": "watch_answer", "message": str(exc)}})
                elif kind == "watch_ice":
                    try:
                        cand = rtc.parse_ice(data or {})
                        await rtc.add_viewer_ice_candidate(room_id, sid, cand)
                    except Exception as exc:
                        log.exception("watch_ice failed room=%s sid=%s", room_id, sid)
                        await ws.send_json({"type": "webrtc_error", "payload": {"stage": "watch_ice", "message": str(exc)}})
        finally:
            if rtc is not None:
                try:
                    await rtc.stop_viewer(room_id, sid)
                except Exception:
                    log.exception("watch disconnect cleanup failed room=%s sid=%s", room_id, sid)

    @app.websocket('/ws/broadcast')
    async def ws_broadcast():
        ws = websocket._get_current_object()
        hub.broadcast_clients.add(ws)
        try:
            while True:
                payload = await websocket.receive_json()
                await hub.publish_broadcast(payload)
        finally:
            hub.broadcast_clients.discard(ws)

    @app.post('/api/upload')
    async def api_upload():
        files = await request.files
        file_storage = files.get("file")
        if not file_storage:
            return jsonify({"error": "file required"}), 400
        return jsonify(await save_upload(file_storage))
