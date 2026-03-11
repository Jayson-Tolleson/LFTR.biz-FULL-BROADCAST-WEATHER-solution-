from pydantic import BaseModel
from typing import Optional, Any


class WsEnvelope(BaseModel):
    type: str
    room: Optional[str] = None
    clientId: Optional[str] = None
    payload: Optional[dict[str, Any]] = None
