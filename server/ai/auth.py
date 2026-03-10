import os
from dataclasses import dataclass
from typing import Optional

try:
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError
except Exception:
    google = None
    class DefaultCredentialsError(Exception):
        pass


@dataclass
class AuthStatus:
    mode: str
    project_id: Optional[str]
    credentials_path: Optional[str]
    reason: str = ""


def _candidate_key_path() -> Optional[str]:
    for key in ("GOOGLE_APPLICATION_CREDENTIALS", "GCP_KEY"):
        val = (os.getenv(key, "") or "").strip()
        if val:
            return val
    return None


def is_explicit_key_available() -> bool:
    path = _candidate_key_path()
    return bool(path and os.path.isfile(path))


def is_adc_available() -> bool:
    if google is None:
        return False
    try:
        google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return True
    except DefaultCredentialsError:
        return False


def maybe_apply_google_credentials_env() -> Optional[str]:
    path = _candidate_key_path()
    if path and os.path.isfile(path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
        return path
    if path and not os.path.isfile(path):
        os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    return None


def get_effective_google_project() -> Optional[str]:
    for key in ("GOOGLE_CLOUD_PROJECT", "VERTEX_PROJECT", "GCP_PROJECT"):
        val = (os.getenv(key, "") or "").strip()
        if val:
            return val
    if google is None:
        return None
    try:
        _, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        return project
    except Exception:
        return None


def resolve_gcp_auth_mode() -> AuthStatus:
    explicit = maybe_apply_google_credentials_env()
    if explicit:
        return AuthStatus(mode="explicit_key_ok", project_id=get_effective_google_project(), credentials_path=explicit)
    if is_adc_available():
        return AuthStatus(mode="adc_ok", project_id=get_effective_google_project(), credentials_path=None)

    missing = _candidate_key_path()
    if missing and not os.path.isfile(missing):
        return AuthStatus(mode="auth_invalid", project_id=get_effective_google_project(), credentials_path=missing, reason="missing_key_file")
    return AuthStatus(mode="auth_missing", project_id=get_effective_google_project(), credentials_path=None)
