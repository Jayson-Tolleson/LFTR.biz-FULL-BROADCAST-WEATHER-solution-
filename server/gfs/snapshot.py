from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SnapshotState:
    last_successful_fetch: datetime | None = None
    last_valid_time: datetime | None = None
    degraded: bool = False
    payloads: dict[str, dict[str, Any]] | None = None

    def mark_success(self, valid_time: datetime | None) -> None:
        self.last_successful_fetch = datetime.utcnow()
        self.last_valid_time = valid_time
        self.degraded = False

    def mark_degraded(self) -> None:
        self.degraded = True
