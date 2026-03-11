from pydantic import BaseModel
from typing import Any


class GFSDiagnosticsResponse(BaseModel):
    ok: bool
    data: dict[str, Any]
