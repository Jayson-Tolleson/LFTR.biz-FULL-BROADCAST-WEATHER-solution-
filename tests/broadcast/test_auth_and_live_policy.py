from __future__ import annotations


import pytest


def test_auth_mode_explicit_key_ok(tmp_path, monkeypatch):
    key = tmp_path / "gcp-key.json"
    key.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GCP_KEY", str(key))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    from server.ai import auth as auth_mod

    assert auth_mod.maybe_apply_google_credentials_env() == str(key)
    assert auth_mod.resolve_gcp_auth_mode() == "explicit_key_ok"


def test_auth_mode_adc_ok_without_key(monkeypatch):
    monkeypatch.delenv("GCP_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    from server.ai import auth as auth_mod

    monkeypatch.setattr(auth_mod, "is_adc_available", lambda: True)
    monkeypatch.setattr(auth_mod, "is_explicit_key_available", lambda: False)

    assert auth_mod.resolve_gcp_auth_mode() == "adc_ok"


def test_missing_key_path_not_poisoned(monkeypatch):
    monkeypatch.setenv("GCP_KEY", "/missing/key.json")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/missing/key2.json")

    from server.ai import auth as auth_mod

    assert auth_mod.maybe_apply_google_credentials_env() is None
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in __import__("os").environ


def test_live_grid_mismatch_raises_named_diagnostic(tmp_path):
    np = pytest.importorskip("numpy")
    from server.gfs_service import GFSService

    svc = GFSService(str(tmp_path))
    with pytest.raises(ValueError, match="live_grid_mismatch field=temperature_k"):
        svc.ensure_same_grid(np.zeros((241, 480)), (721, 1440), "temperature_k")


def test_fallback_disabled_raises(tmp_path, monkeypatch):
    from server import gfs_service as gfs_mod

    svc = gfs_mod.GFSService(str(tmp_path))
    monkeypatch.setattr(gfs_mod, "ALLOW_SYNTHETIC_FALLBACK", False)
    monkeypatch.setattr(svc, "generate_real_gfs_payload", lambda bbox: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(svc, "read_most_recent_cached_real_payload", lambda max_age_seconds=5400: None)

    with pytest.raises(RuntimeError, match="live_only_mode_failed reason=boom"):
        svc._generate_weather_payload_uncached(None)


def test_watch_waiting_message_used_not_error_literal():
    text = __import__("pathlib").Path("server/broadcast/routes.py").read_text(encoding="utf-8")
    assert '"type": "waiting"' in text
