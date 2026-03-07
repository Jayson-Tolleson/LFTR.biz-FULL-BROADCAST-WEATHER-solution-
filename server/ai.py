from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional, Sequence

from quart import Response


log = logging.getLogger("server.ai")


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
    """Call Google Speech streaming API across client signature variants.

    Some client builds require `config=..., requests=...`, while others expect
    only `requests` with a leading config request.
    """
    try:
        return client.streaming_recognize(config=streaming_config, requests=iter(requests))
    except TypeError:
        pass

    try:
        return client.streaming_recognize(requests=iter(requests))
    except TypeError:
        pass

    try:
        return client.streaming_recognize(iter(requests))
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
    """Transcribe one or more encoded audio chunks to text.

    Returns an empty string when STT providers are unavailable, allowing callers
    to keep socket/event compatibility without hard failures.
    """
    return await asyncio.to_thread(_transcribe_sync, list(chunks), mime)


def stt_available() -> bool:
    try:
        from google.cloud import speech  # noqa: F401
        return True
    except Exception:
        return False
