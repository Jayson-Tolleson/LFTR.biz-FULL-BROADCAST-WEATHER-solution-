from __future__ import annotations

import base64
from pathlib import Path
from uuid import uuid4


AUDIO_DIR = Path(__file__).resolve().parent.parent.parent / "uploads" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)


def synthesize_voice(text: str) -> str:
    token = uuid4().hex[:10]
    out = AUDIO_DIR / f"ai_{token}.mp3"
    out.write_bytes(b"")
    return f"/uploads/audio/{out.name}"


def transcribe_audio_chunk(audio_b64: str) -> str:
    try:
        base64.b64decode(audio_b64)
    except Exception:
        return ""
    return "[transcribed speech]"
