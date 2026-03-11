from __future__ import annotations

import base64
import logging
from pathlib import Path
from uuid import uuid4

from .auth import maybe_apply_google_credentials_env, resolve_gcp_auth_mode


log = logging.getLogger("server.ai.speech")
AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def _credentials_ready() -> bool:
    maybe_apply_google_credentials_env()
    return resolve_gcp_auth_mode() in {"adc_ok", "explicit_key_ok"}


def synthesize_voice(text: str) -> str:
    token = uuid4().hex[:10]
    out = AUDIO_DIR / f"ai_{token}.mp3"

    if not text:
        out.write_bytes(b"")
        return f"/uploads/audio/{out.name}"

    if not _credentials_ready():
        out.write_bytes(b"")
        return f"/uploads/audio/{out.name}"

    try:
        from google.cloud import texttospeech

        client = texttospeech.TextToSpeechClient()
        input_text = texttospeech.SynthesisInput(text=text)
        voice = texttospeech.VoiceSelectionParams(language_code="en-US", name="en-US-Neural2-C")
        audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.MP3)

        response = client.synthesize_speech(input=input_text, voice=voice, audio_config=audio_config)
        out.write_bytes(response.audio_content or b"")
    except Exception:
        log.exception("google tts failed")
        out.write_bytes(b"")

    return f"/uploads/audio/{out.name}"


def transcribe_audio_chunk(audio_b64: str) -> str:
    if not audio_b64:
        return ""

    try:
        audio_bytes = base64.b64decode(audio_b64)
    except Exception:
        return ""

    if not audio_bytes:
        return ""

    if not _credentials_ready():
        return ""

    try:
        from google.cloud import speech

        client = speech.SpeechClient()
        recognition_config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
            sample_rate_hertz=48000,
            language_code="en-US",
            enable_automatic_punctuation=True,
            model="latest_long",
        )
        streaming_config = speech.StreamingRecognitionConfig(config=recognition_config, interim_results=False)

        requests = [speech.StreamingRecognizeRequest(audio_content=audio_bytes)]
        responses = client.streaming_recognize(config=streaming_config, requests=iter(requests))

        parts: list[str] = []
        for response in responses:
            for result in response.results:
                if result.alternatives:
                    transcript = (result.alternatives[0].transcript or "").strip()
                    if transcript:
                        parts.append(transcript)
        return " ".join(parts).strip()
    except Exception:
        log.exception("google stt failed")
        return ""
