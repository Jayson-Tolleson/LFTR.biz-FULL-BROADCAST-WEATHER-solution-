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
    # Scene/tile cache used by tile endpoints to avoid recomputing full derivation per request.
    scene_cache: Optional[Dict[str, Any]] = None
    scene_cache_ts: Optional[int] = None
    tile_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    model_cycle: Optional[str] = None
    model_forecast_hour: Optional[int] = None
    model_valid_time: Optional[str] = None
    model_analysis_time: Optional[str] = None
    model_source_url: Optional[str] = None
    model_cache_path: Optional[str] = None
    model_source_format: str = "grib2"
    ingest_last_attempt_ts: Optional[int] = None
    ingest_last_success_ts: Optional[int] = None
    ingest_status: str = "idle"
    ingest_error: Optional[str] = None
    degraded_mode: bool = True
    using_last_known_good: bool = False
    fields_available: List[str] = field(default_factory=list)
    fields_missing: List[str] = field(default_factory=list)
    last_good_model_state: Optional[Dict[str, Any]] = None
    decode_backend: str = "none"
    data_source_mode: str = "heuristic"
    tile_diagnostics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    layer_feature_index: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    layer_feature_meta: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
    layer_feature_store: Dict[str, Dict[str, Dict[str, Any]]] = field(default_factory=dict)
