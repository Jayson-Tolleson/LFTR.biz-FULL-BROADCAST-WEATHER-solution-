from __future__ import annotations

from datetime import datetime
from typing import Any

from server.gfs.models import BBox


class RtofsProvider:
    """Stub ocean forcing provider. TODO: integrate real RTOFS subsets."""

    async def fetch_subset(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        return {"currents": [], "sst": []}, valid_time

    def health(self) -> dict[str, Any]:
        return {"provider": "rtofs", "status": "stub"}
