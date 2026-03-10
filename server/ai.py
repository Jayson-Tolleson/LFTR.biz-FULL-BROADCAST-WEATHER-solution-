from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
import urllib.parse
from typing import Any, Dict, Iterable, List, Sequence

from quart import Response

from server.ai.gemini import generate_ai_reply, provider_name
from server.ai.speech import synthesize_voice


log = logging.getLogger("server.ai")


def _json_response(payload: Dict[str, Any], status: int = 200) -> Response:
    return Response(json.dumps(payload), status=status, content_type="application/json")


async def handle_chat(payload: Dict[str, Any], fallback_text: str) -> Dict[str, Any]:
    text = ((payload or {}).get("text") or "").strip()
    if not text:
        return {"ok": False, "provider": "none", "error": "missing_text", "message": "Please provide text.", "data": {"reply": "", "command": None}}

    try:
        reply = await asyncio.to_thread(lambda: generate_ai_reply(text)["text"])
    except Exception as exc:
        log.exception("chat provider request failed")
        return {
            "ok": False,
            "provider": provider_name(),
            "error": "provider_request_failed",
            "message": str(exc),
            "data": {"reply": fallback_text, "command": None},
        }

    return {
        "ok": True,
        "provider": provider_name(),
        "error": None,
        "message": "ok",
        "data": {"reply": reply or fallback_text, "command": None},
    }


async def handle_tts(payload: Dict[str, Any]) -> Response:
    text = ((payload or {}).get("text") or "").strip()
    if not text:
        return _json_response({"ok": False, "provider": "none", "error": "missing_text", "message": "text is required", "data": None}, 400)

    try:
        voice_url = await asyncio.to_thread(synthesize_voice, text)
        return _json_response({"ok": True, "provider": provider_name(), "error": None, "message": "ok", "data": {"voice": voice_url}})
    except Exception as exc:
        log.exception("tts provider request failed")
        return _json_response({"ok": False, "provider": provider_name(), "error": "provider_request_failed", "message": str(exc), "data": None}, 500)


async def handle_websearch(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = ((payload or {}).get("query") or "").strip()
    if not query:
        return {"ok": False, "provider": "none", "error": "missing_query", "message": "query is required", "data": {"results": []}}

    serpapi_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not serpapi_key:
        return {
            "ok": False,
            "provider": "none",
            "error": "provider_unconfigured",
            "message": "Web search provider is not configured. Set SERPAPI_API_KEY.",
            "data": {"query": query, "results": []},
        }

    def _fetch() -> Dict[str, Any]:
        q = urllib.parse.urlencode({"q": query, "api_key": serpapi_key, "engine": "google", "num": 5})
        url = f"https://serpapi.com/search.json?{q}"
        with urllib.request.urlopen(url, timeout=8) as resp:
            body = json.loads(resp.read().decode("utf-8", errors="replace"))
        organic = body.get("organic_results") or []
        results = [
            {
                "title": r.get("title") or "",
                "url": r.get("link") or "",
                "snippet": r.get("snippet") or "",
            }
            for r in organic[:5]
        ]
        return {"ok": True, "provider": "serpapi", "error": None, "message": "ok", "data": {"query": query, "results": results}}

    try:
        return await asyncio.to_thread(_fetch)
    except Exception as exc:
        log.exception("websearch provider request failed")
        return {"ok": False, "provider": "serpapi", "error": "provider_request_failed", "message": str(exc), "data": {"query": query, "results": []}}


def _extract_transcript(responses: Iterable[Any]) -> str:
    parts: List[str] = []
    for response in responses:
        for result in getattr(response, "results", []) or []:
            alternatives = getattr(result, "alternatives", []) or []
            if alternatives:
                transcript = (getattr(alternatives[0], "transcript", "") or "").strip()
                if transcript:
                    parts.append(transcript)
    return " ".join(parts).strip()


def _streaming_recognize_with_google(client: Any, streaming_config: Any, requests: Sequence[Any]) -> Iterable[Any]:
    try:
        return client.streaming_recognize(streaming_config, iter(requests))
    except TypeError:
        pass

    try:
        return client.streaming_recognize(config=streaming_config, requests=iter(requests))
    except TypeError as exc:
        raise RuntimeError("Unsupported streaming_recognize client signature") from exc


def _transcribe_sync(chunks: Sequence[bytes], mime: str = "audio/webm") -> str:
    if not chunks:
        return ""

    try:
        from google.cloud import speech
    except Exception:
        log.debug("google.cloud.speech unavailable; skipping STT")
        return ""

    try:
        client = speech.SpeechClient()
        encoding = speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
        if "ogg" in (mime or "").lower():
            encoding = speech.RecognitionConfig.AudioEncoding.OGG_OPUS

        recognition_config = speech.RecognitionConfig(
            encoding=encoding,
            sample_rate_hertz=48000,
            language_code="en-US",
            enable_automatic_punctuation=True,
            model="latest_long",
        )
        streaming_config = speech.StreamingRecognitionConfig(
            config=recognition_config,
            interim_results=False,
            single_utterance=False,
        )

        audio_requests = [speech.StreamingRecognizeRequest(audio_content=c) for c in chunks if c]
        if not audio_requests:
            return ""

        requests_with_leading_config = [
            speech.StreamingRecognizeRequest(streaming_config=streaming_config),
            *audio_requests,
        ]

        try:
            responses = _streaming_recognize_with_google(client, streaming_config, audio_requests)
        except RuntimeError:
            responses = _streaming_recognize_with_google(client, streaming_config, requests_with_leading_config)

        return _extract_transcript(responses)
    except Exception:
        log.exception("google streaming transcribe failed")
        return ""


async def transcribe_track(chunks: Sequence[bytes], mime: str = "audio/webm") -> str:
    return await asyncio.to_thread(_transcribe_sync, list(chunks), mime)


def stt_available() -> bool:
    try:
        from google.cloud import speech  # noqa: F401
        return True
    except Exception:
        return False
