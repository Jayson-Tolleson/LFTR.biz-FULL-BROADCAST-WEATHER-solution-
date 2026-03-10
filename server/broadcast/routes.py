from __future__ import annotations

from quart import jsonify, request, websocket

from server.ai.gemini import generate_ai_reply, stream_ai_reply
from server.ai.speech import synthesize_voice, transcribe_audio_chunk
from server.broadcast.websocket import hub
from server.media.upload import save_upload


def register_broadcast_routes(app) -> None:
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
