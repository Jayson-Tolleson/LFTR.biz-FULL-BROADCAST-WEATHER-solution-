from __future__ import annotations

from datetime import datetime
from typing import Any

from server.gfs.models import BBox


class CoastwatchProvider:
    """Stub chlorophyll provider. TODO: integrate CoastWatch subsets."""

    async def fetch_subset(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        return {"chlorophyll": []}, valid_time

    def health(self) -> dict[str, Any]:
        return {"provider": "coastwatch", "status": "stub"}
