from __future__ import annotations

from datetime import datetime
from typing import Any


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() + "Z" if value else None


def serialize_weather(*, valid_time: datetime | None, bbox: list[float], stride: int, fields: dict[str, Any], stale: bool) -> dict[str, Any]:
    sample = next(iter(fields.values()), [])
    ny = len(sample) if isinstance(sample, list) else 0
    nx = len(sample[0]) if ny and isinstance(sample[0], list) else 0
    return {
        "valid_time": _iso(valid_time),
        "bbox": bbox,
        "stride": stride,
        "grid": {"nx": nx, "ny": ny, "dx": 0.25 * stride, "dy": 0.25 * stride},
        "fields": fields,
        "stale": stale,
    }


def serialize_clouds(*, valid_time: datetime | None, bbox: list[float], layers: list[dict[str, Any]], convective: dict[str, Any], stale: bool) -> dict[str, Any]:
    return {
        "valid_time": _iso(valid_time),
        "bbox": bbox,
        "cloud_layers": layers,
        "convective": convective,
        "stale": stale,
    }


def serialize_bait(*, valid_time: datetime | None, bbox: list[float], bait_score: Any, fronts: list[Any], convergence_polygons: list[Any], boil_probability_polygons: list[Any], confidence: dict[str, float], stale: bool) -> dict[str, Any]:
    return {
        "valid_time": _iso(valid_time),
        "bbox": bbox,
        "bait_score": bait_score,
        "front_lines": fronts,
        "convergence_polygons": convergence_polygons,
        "boil_probability_polygons": boil_probability_polygons,
        "confidence": confidence,
        "stale": stale,
    }
