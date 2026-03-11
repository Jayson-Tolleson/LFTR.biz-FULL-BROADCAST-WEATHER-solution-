from pydantic import BaseModel
from typing import Optional, Any


class HealthResponse(BaseModel):
    ok: bool


class AIStatusResponse(BaseModel):
    ai_available: bool
    ai_enabled: bool
    provider: str
    tts_available: bool
    stt_available: bool
    google_auth: dict[str, Any]


class UploadResponse(BaseModel):
    url: str


class BBoxParams(BaseModel):
    west: Optional[float] = None
    south: Optional[float] = None
    east: Optional[float] = None
    north: Optional[float] = None
