from __future__ import annotations

import asyncio
import logging
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, Sequence

from .gemini import generate_ai_reply, provider_name
from .speech import synthesize_voice, transcribe_audio_chunk
from .auth import auth_status_payload, resolve_gcp_auth_mode

log = logging.getLogger("server.ai.pkg")
_WARNED_STT_UNAVAILABLE = False


async def handle_chat(payload: Dict[str, Any], fallback_text: str) -> Dict[str, Any]:
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        return {"ok": False, "provider": "none", "error": "missing_text", "message": "Please provide text.", "data": {"reply": "", "command": None}}
    try:
        response = await asyncio.to_thread(generate_ai_reply, text)
        reply = str((response or {}).get("text") or "").strip() or fallback_text
        return {"ok": True, "provider": provider_name(), "error": None, "message": "ok", "data": {"reply": reply, "command": None}}
    except Exception as exc:
        log.exception("ai chat provider request failed")
        return {"ok": False, "provider": provider_name(), "error": "provider_request_failed", "message": str(exc), "data": {"reply": fallback_text, "command": None}}


async def handle_websearch(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = str((payload or {}).get("query") or "").strip()
    if not query:
        return {"ok": False, "provider": "none", "error": "missing_query", "message": "query is required", "data": {"query": "", "results": []}}

    serpapi_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not serpapi_key:
        return {"ok": False, "provider": "none", "error": "provider_unconfigured", "message": "SERPAPI_API_KEY missing", "data": {"query": query, "results": []}}

    def _fetch() -> Dict[str, Any]:
        q = urllib.parse.urlencode({"q": query, "api_key": serpapi_key, "engine": "google", "num": 5})
        with urllib.request.urlopen(f"https://serpapi.com/search.json?{q}", timeout=8) as resp:
            import json

            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        organic = body.get("organic_results") or []
        rows = [{"title": r.get("title") or "", "url": r.get("link") or "", "snippet": r.get("snippet") or ""} for r in organic[:5]]
        return {"ok": True, "provider": "serpapi", "error": None, "message": "ok", "data": {"query": query, "results": rows}}

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        log.exception("websearch provider request failed")
        return {"ok": False, "provider": "serpapi", "error": "provider_request_failed", "message": str(exc), "data": {"query": query, "results": []}}




def stt_available() -> bool:
    try:
        from google.cloud import speech  # noqa: F401
        return resolve_gcp_auth_mode() in {"adc_ok", "explicit_key_ok"}
    except Exception:
        return False


def ai_status() -> dict[str, Any]:
    status = auth_status_payload()
    return {
        **status,
        "stt_ready": stt_available(),
    }
async def transcribe_track(chunks: Sequence[bytes], mime: str | None = None) -> str:
    global _WARNED_STT_UNAVAILABLE
    if not chunks:
        return ""
    mime = (mime or "audio/webm").lower()

    chunk = b"".join([c for c in chunks if c])
    if not chunk:
        return ""

    try:
        import base64

        b64 = base64.b64encode(chunk).decode("ascii")
        transcript = await asyncio.to_thread(transcribe_audio_chunk, b64)
        return str(transcript or "").strip()
    except Exception:
        if not _WARNED_STT_UNAVAILABLE:
            _WARNED_STT_UNAVAILABLE = True
            log.warning("STT backend unavailable; transcribe_track will return empty transcript")
        log.debug("transcribe_track failed", exc_info=True)
        return ""


__all__ = [
    "generate_ai_reply",
    "provider_name",
    "synthesize_voice",
    "handle_chat",
    "handle_websearch",
    "transcribe_track",
    "stt_available",
    "ai_status",
]
