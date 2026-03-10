import os

from server.ai.auth import maybe_apply_google_credentials_env, resolve_gcp_auth_mode


def test_missing_key_not_forced(monkeypatch):
    monkeypatch.setenv("GCP_KEY", "/tmp/does-not-exist.json")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    maybe_apply_google_credentials_env()
    assert os.getenv("GOOGLE_APPLICATION_CREDENTIALS") is None
    assert resolve_gcp_auth_mode().mode in {"auth_invalid", "adc_ok", "auth_missing"}


def test_explicit_key(monkeypatch, tmp_path):
    key = tmp_path / "k.json"
    key.write_text("{}")
    monkeypatch.setenv("GCP_KEY", str(key))
    mode = resolve_gcp_auth_mode().mode
    assert mode == "explicit_key_ok"
