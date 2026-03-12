from __future__ import annotations

import math

R_E = 6_371_000.0


def deg_to_rad(lat_deg: float, lon_deg: float) -> tuple[float, float]:
    φ = float(lat_deg) * math.pi / 180.0
    λ = float(lon_deg) * math.pi / 180.0
    return φ, λ


def rad_to_deg(phi: float, lam: float) -> tuple[float, float]:
    lat_deg = float(phi) * 180.0 / math.pi
    lon_deg = float(lam) * 180.0 / math.pi
    return lat_deg, lon_deg


def wrapped_longitude(lam: float) -> float:
    λ = float(lam)
    return ((λ + math.pi) % (2.0 * math.pi) + 2.0 * math.pi) % (2.0 * math.pi) - math.pi


def bearing_from_uv(u: float, v: float) -> float:
    θ = math.atan2(float(u), float(v))
    return θ


def cell_offsets(cell_size_deg: float) -> tuple[float, float]:
    Δφ = float(cell_size_deg) * math.pi / 180.0
    Δλ = float(cell_size_deg) * math.pi / 180.0
    return Δφ, Δλ
