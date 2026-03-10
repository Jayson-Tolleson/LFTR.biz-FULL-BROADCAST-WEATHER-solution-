from .auth import resolve_gcp_auth_mode


def readiness() -> dict:
    status = resolve_gcp_auth_mode()
    return {
        "provider": "gemini",
        "auth_mode": status.mode,
        "project": status.project_id,
        "ready": status.mode in {"adc_ok", "explicit_key_ok"},
        "reason": status.reason,
    }
