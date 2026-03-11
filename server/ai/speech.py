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


_LOGGED_STT_FORMATS: set[str] = set()

_OPUS_SUPPORTED_SAMPLE_RATES = {8000, 12000, 16000, 24000, 48000}


def _normalize_audio_config(encoding_name: str, sample_rate_hz: int | None, channels: int | None) -> tuple[int | None, int]:
    enc = str(encoding_name or '').upper()
    ch = 1
    try:
        ch = int(channels or 1)
    except Exception:
        ch = 1
    if ch < 1:
        ch = 1

    sr = None
    try:
        sr = int(sample_rate_hz) if sample_rate_hz is not None else None
    except Exception:
        sr = None

    if enc in {'WEBM_OPUS', 'OGG_OPUS', 'OPUS'}:
        if sr not in _OPUS_SUPPORTED_SAMPLE_RATES:
            sr = 48000
        return sr, ch

    if sr is not None and sr <= 0:
        sr = None
    return sr, ch


def _stt_encoding_from_mime(mime: str):
    from google.cloud import speech

    m = (mime or "").lower()
    if "ogg" in m and "opus" in m:
        return speech.RecognitionConfig.AudioEncoding.OGG_OPUS
    if "webm" in m and "opus" in m:
        return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
    if "wav" in m or "x-wav" in m:
        return speech.RecognitionConfig.AudioEncoding.LINEAR16
    return speech.RecognitionConfig.AudioEncoding.WEBM_OPUS


def transcribe_audio_chunk(audio_b64: str, *, mime: str = "audio/webm;codecs=opus", sample_rate_hz: int | None = None, channels: int | None = None, language_code: str = "en-US") -> str:
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

        encoding = _stt_encoding_from_mime(mime)
        sample_rate, channel_count = _normalize_audio_config(encoding.name, sample_rate_hz, channels)

        config_kwargs = {
            "encoding": encoding,
            "language_code": language_code or "en-US",
            "enable_automatic_punctuation": True,
            "audio_channel_count": channel_count,
        }
        if sample_rate is not None:
            config_kwargs["sample_rate_hertz"] = int(sample_rate)

        key = f"{mime}|{config_kwargs.get('sample_rate_hertz', 'none')}|{channel_count}|{language_code}"
        if key not in _LOGGED_STT_FORMATS:
            _LOGGED_STT_FORMATS.add(key)
            log.info("google stt config encoding=%s sample_rate_hertz=%s channels=%s language=%s", encoding.name, config_kwargs.get("sample_rate_hertz", "none"), channel_count, language_code)

        client = speech.SpeechClient()
        recognition_config = speech.RecognitionConfig(**config_kwargs)
        response = client.recognize(config=recognition_config, audio=speech.RecognitionAudio(content=audio_bytes))

        parts: list[str] = []
        for result in (response.results or []):
            if result.alternatives:
                transcript = (result.alternatives[0].transcript or "").strip()
                if transcript:
                    parts.append(transcript)
        return " ".join(parts).strip()
    except Exception:
        log.exception("google stt failed")
        return ""
