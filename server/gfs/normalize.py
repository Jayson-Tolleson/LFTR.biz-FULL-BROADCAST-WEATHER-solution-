from __future__ import annotations

from typing import Any


def to_number(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
        if n != n:
            return default
        return n
    except Exception:
        return default


def clamp(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def normalize_lon_deg(lon_deg: float) -> float:
    lon = float(lon_deg)
    while lon < -180.0:
        lon += 360.0
    while lon >= 180.0:
        lon -= 360.0
    return lon


def sanitize_confidence(value: Any) -> int:
    n = to_number(value, 0.0)
    n = clamp(n, 0.0, 1.0)
    return int(round(n * 255.0))
