from __future__ import annotations

from server.ai.speech import synthesize_voice


def generate_ai_reply(text: str) -> dict:
    prompt = (text or "").strip()
    if not prompt:
        reply = "I didn't catch that."
    else:
        reply = f"AI: Based on current feed, {prompt}"
    return {"text": reply, "voice": synthesize_voice(reply)}
