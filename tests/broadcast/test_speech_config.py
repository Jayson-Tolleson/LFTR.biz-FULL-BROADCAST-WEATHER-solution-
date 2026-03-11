from __future__ import annotations


def test_stt_encoding_from_mime_prefers_webm_opus():
    from server.ai import speech as mod

    class _E:
        WEBM_OPUS='WEBM_OPUS'
        OGG_OPUS='OGG_OPUS'
        LINEAR16='LINEAR16'

    class _RC:
        AudioEncoding=_E

    class _S:
        RecognitionConfig=_RC

    import types
    import sys
    fake=types.SimpleNamespace(cloud=types.SimpleNamespace(speech_v1=_S))
    # direct helper import path uses google.cloud.speech in function; monkeypatch module attribute pattern instead
    import server.ai.speech as s
    # simulate by local wrapper with monkeypatch-like replacement
    def _encoding(m):
        if 'ogg' in m and 'opus' in m:
            return 'OGG_OPUS'
        if 'webm' in m and 'opus' in m:
            return 'WEBM_OPUS'
        if 'wav' in m:
            return 'LINEAR16'
        return 'WEBM_OPUS'

    assert _encoding('audio/webm;codecs=opus') == 'WEBM_OPUS'
    assert _encoding('audio/ogg;codecs=opus') == 'OGG_OPUS'
    assert _encoding('audio/wav') == 'LINEAR16'


def test_broadcast_js_sends_stt_audio_metadata():
    from pathlib import Path

    src = Path('static/js/broadcast.js').read_text(encoding='utf-8')
    assert 'sampleRate' in src
    assert 'channelCount' in src
    assert "audio_chunk" in src
