from __future__ import annotations

from typing import Any, Dict, List

from quart import Response


async def handle_chat(payload: Dict[str, Any], fallback_text: str) -> Dict[str, Any]:
    text = (payload or {}).get("text", "")
    text = (text or "").strip()
    if not text:
        return {"reply": "Please provide text.", "command": None}
    return {"reply": fallback_text, "command": None}


async def handle_tts(payload: Dict[str, Any]) -> Response:
    # Compatibility-first placeholder response (silent wav header bytes)
    text = ((payload or {}).get("text") or "").strip()
    if not text:
        text = ""
    # tiny empty WAV-ish payload (clients tolerate empty/short audio)
    audio = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    return Response(audio, content_type="audio/wav")


async def handle_websearch(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = (payload or {}).get("query", "")
    query = (query or "").strip()
    return {
        "query": query,
        "results": [
            {
                "title": "Mock result",
                "url": "https://example.com",
                "snippet": f"No API configured for: {query}",
            }
        ],
    }
