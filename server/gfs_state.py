from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class GFSState:
    enabled: bool = True
    source_name: str = "gfs-module"
    cache_ttl_seconds: int = 300
    last_refresh_ts: Optional[int] = None
    last_error: Optional[str] = None
    fish_points: List[Dict[str, Any]] = field(default_factory=list)
