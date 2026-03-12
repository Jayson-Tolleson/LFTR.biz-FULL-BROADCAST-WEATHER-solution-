from __future__ import annotations

import pytest

from server.audio.validation import AudioValidationError, decode_audio_chunk_payload


def test_decode_audio_chunk_payload_accepts_pcm16_contract():
    import base64

    payload = {
        'encoding': 'linear16',
        'sampleRate': 16000,
        'channels': 1,
        'data': base64.b64encode(b'\x00\x00\x01\x00').decode('ascii'),
    }
    parsed = decode_audio_chunk_payload(payload)
    assert parsed.encoding == 'linear16'
    assert parsed.sample_rate_hz == 16000
    assert parsed.channels == 1
    assert parsed.audio_bytes == b'\x00\x00\x01\x00'


def test_decode_audio_chunk_payload_rejects_non_pcm_contract():
    with pytest.raises(AudioValidationError):
        decode_audio_chunk_payload({'encoding': 'webm_opus', 'sampleRate': 16000, 'channels': 1, 'data': 'AA=='})


def test_broadcast_js_sends_linear16_sidecar_contract():
    from pathlib import Path

    src = Path('static/js/broadcast.js').read_text(encoding='utf-8')
    assert "encoding: 'linear16'" in src
    assert 'const STT_TARGET_SAMPLE_RATE = 16000;' in src
    assert 'createScriptProcessor(4096, 1, 1)' in src
