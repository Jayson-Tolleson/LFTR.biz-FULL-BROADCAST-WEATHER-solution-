from .auth import resolve_gcp_auth_mode


def readiness() -> dict:
    status = resolve_gcp_auth_mode()
    return {
        "provider": "speech_to_text",
        "auth_mode": status.mode,
        "project": status.project_id,
        "ready": status.mode in {"adc_ok", "explicit_key_ok"},
        "reason": status.reason,
    }


async def transcribe_track(*_args, **_kwargs):
    return ""
