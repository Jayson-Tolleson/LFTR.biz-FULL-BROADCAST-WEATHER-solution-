from __future__ import annotations


def test_normalize_audio_config_missing_sample_rate_defaults_opus_48000():
    from server.ai import speech as mod

    sr, ch = mod._normalize_audio_config('WEBM_OPUS', None, 1)
    assert sr == 48000
    assert ch == 1


def test_normalize_audio_config_zero_sample_rate_defaults_opus_48000():
    from server.ai import speech as mod

    sr, ch = mod._normalize_audio_config('OGG_OPUS', 0, 2)
    assert sr == 48000
    assert ch == 2


def test_normalize_audio_config_invalid_opus_rate_defaults_48000():
    from server.ai import speech as mod

    sr, ch = mod._normalize_audio_config('WEBM_OPUS', 44100, 1)
    assert sr == 48000
    assert ch == 1


def test_normalize_audio_config_valid_opus_rate_kept():
    from server.ai import speech as mod

    sr, ch = mod._normalize_audio_config('WEBM_OPUS', 48000, 1)
    assert sr == 48000
    assert ch == 1


def test_broadcast_js_sends_explicit_nonzero_samplerate_metadata():
    from pathlib import Path

    src = Path('static/js/broadcast.js').read_text(encoding='utf-8')
    assert 'sampleRate = rawSampleRate > 0 ? rawSampleRate : 48000' in src
    assert 'sampleRate, channels' in src
