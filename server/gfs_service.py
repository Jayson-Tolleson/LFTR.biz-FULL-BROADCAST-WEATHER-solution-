from __future__ import annotations

import base64
import gc
import csv
import hashlib
import json
import math
import random
import re
import time
import threading
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from typing import Any, Dict, List, Tuple

try:
    import numpy as np
except Exception:  # pragma: no cover - fallback mode
    np = None

try:
    import xarray as xr
except Exception:  # pragma: no cover - fallback mode
    xr = None

try:
    from diskcache import Cache as DiskCache
except Exception:  # pragma: no cover - fallback mode
    DiskCache = None

try:
    import pygrib
except Exception:  # pragma: no cover - optional fallback decoder
    pygrib = None

from werkzeug.utils import secure_filename

from server.gfs_state import GFSState


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
FISH_CSV = DATA_DIR / "fishloclist.csv"


_TRANSPARENT_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgB5o2a4AAAAASUVORK5CYII="
)
_ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}

DEFAULT_HTTP_TIMEOUT = 6.5
ENV_CACHE_TTL_SECONDS = 900
NWS_CACHE_TTL_SECONDS = 1800
TIDE_CACHE_TTL_SECONDS = 1200
SST_CACHE_TTL_SECONDS = 10_800
NWS_API_BASE = "https://api.weather.gov"
NOAA_TIDES_API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_COOPS_MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DEFAULT_UA = "LFTR-GFS/1.0 (+https://lftr.biz)"
DEFAULT_WORLD_ENV_MARKER = {"lat": 34.2, "lon": -120.0}

NOMADS_FILTER_BASE = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
DEFAULT_GFS_TIMEOUT_SECONDS = 18
DEFAULT_GFS_RETRIES = 3
DEFAULT_GFS_CACHE_DIR = BASE_DIR / ".cache" / "gfs_nomads"
DEFAULT_GFS_CACHE_TTL_SECONDS = 60 * 30
DEFAULT_GFS_CYCLE_AVAILABILITY_DELAY_MINUTES = 290
INGEST_FALLBACK_CYCLE_DEPTH = 4
INGEST_PREFERRED_FORECAST_HOUR = 0
INGEST_CACHE_MIN_BYTES = 2000
INGEST_MIN_INTERVAL_SECONDS = 600

SURFACE_VARIABLES = ["PRATE", "APCP", "TCDC", "CAPE", "CIN", "PRMSL", "TMP", "RH", "GUST", "UGRD", "VGRD"]
AGL_VARIABLES = ["TMP", "RH", "UGRD", "VGRD", "TCDC"]
ISOBARIC_VARIABLES = ["RH", "TMP", "HGT", "UGRD", "VGRD"]
DEFAULT_REQUIRED_VARIABLES = sorted(set(SURFACE_VARIABLES + AGL_VARIABLES + ISOBARIC_VARIABLES))
DEFAULT_REQUIRED_LEVELS = ["surface", "2_m_above_ground", "10_m_above_ground", "1000_mb", "925_mb", "850_mb", "700_mb", "500_mb", "300_mb"]

PRECIP_BUCKETS_MM_HR = [0.15, 0.7, 2.5, 8.0, 18.0, 40.0]
SPATIAL_GRID_DEG = 8.0
MAX_TILE_DIAGNOSTICS = 600

log = logging.getLogger("server.gfs")


def safe_float(v, default=0.0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(f):
        return default
    return f


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(timezone.utc)


def floor_to_cycle(dt_utc: datetime) -> int:
    """Return GFS cycle hour bucket (00/06/12/18)."""
    h = dt_utc.hour
    return (h // 6) * 6


def candidate_cycles(dt_utc: datetime) -> list[tuple[str, int]]:
    """Return cycle candidates from newest to older with availability delay."""
    delayed = dt_utc - timedelta(minutes=DEFAULT_GFS_CYCLE_AVAILABILITY_DELAY_MINUTES)
    cur = delayed.replace(minute=0, second=0, microsecond=0)
    cur = cur.replace(hour=floor_to_cycle(cur))
    out: list[tuple[str, int]] = []
    for i in range(0, 4):
        cdt = cur - timedelta(hours=6 * i)
        out.append((cdt.strftime("%Y%m%d"), cdt.hour))
    return out


def nearest_forecast_hour(valid_dt_utc: datetime, cycle_dt_utc: datetime) -> int:
    """Return nearest whole forecast hour from cycle to valid time."""
    return int(round((valid_dt_utc - cycle_dt_utc).total_seconds() / 3600.0))


def clamp_forecast_hour(fhr: int, min_hour: int = 0, max_hour: int = 384) -> int:
    """Clamp forecast hour to legal GFS range."""
    return max(min_hour, min(max_hour, int(fhr)))


class FetchResult:
    def __init__(self, ok: bool, path: Path | None = None, cycle: str = "", forecast_hour: int = 0, valid_time: str = "", error: str = "", url: str = "") -> None:
        self.ok = ok
        self.path = path
        self.cycle = cycle
        self.forecast_hour = forecast_hour
        self.valid_time = valid_time
        self.error = error
        self.url = url


class GFSNomadsClient:
    """NOAA NOMADS GFS 0.25 subset client with on-disk cache."""

    def __init__(self, cache_dir: Path, timeout_seconds: int = DEFAULT_GFS_TIMEOUT_SECONDS, retries: int = DEFAULT_GFS_RETRIES):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": DEFAULT_UA})

    def build_file_name(self, cycle_hour: int, forecast_hour: int) -> str:
        return f"gfs.t{int(cycle_hour):02d}z.pgrb2.0p25.f{int(forecast_hour):03d}"

    def build_dir(self, date_str: str, cycle_hour: int) -> str:
        return f"/gfs.{date_str}/{int(cycle_hour):02d}/atmos"

    def normalize_bbox_for_nomads(self, bbox: dict[str, float]) -> dict[str, float]:
        _ = bbox
        return {"leftlon": -180.0, "rightlon": 180.0, "toplat": 80.0, "bottomlat": -80.0}

    def build_filter_url(self, date_str: str, cycle_hour: int, forecast_hour: int, bbox: dict[str, float], variables: list[str], levels: list[str]) -> str:
        from urllib.parse import urlencode

        file_name = self.build_file_name(cycle_hour, forecast_hour)
        dir_name = self.build_dir(date_str, cycle_hour)
        query: dict[str, Any] = {"file": file_name, "dir": dir_name}
        b = self.normalize_bbox_for_nomads(bbox)
        query.update(b)
        for var in variables:
            query[f"var_{var}"] = "on"
        for lvl in levels:
            query[f"lev_{lvl}"] = "on"
        return f"{NOMADS_FILTER_BASE}?{urlencode(query)}"

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.grib2"

    def fetch_subset(self, url: str, out_path: Path) -> Path:
        tmp = out_path.with_suffix(".tmp")
        for attempt in range(self.retries):
            try:
                resp = self.http.get(url, timeout=self.timeout_seconds)
                resp.raise_for_status()
                ctype = (resp.headers.get("Content-Type") or "").lower()
                body = resp.content
                if b"<html" in body[:200].lower() or ("text/html" in ctype):
                    raise RuntimeError("nomads returned html page")
                if len(body) < 2000:
                    raise RuntimeError("nomads subset too small")
                tmp.write_bytes(body)
                tmp.replace(out_path)
                return out_path
            except Exception:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)
                if attempt >= self.retries - 1:
                    raise
                time.sleep(0.4 * (2 ** attempt))
        return out_path

    def fetch_latest_available_subset(self, target_dt_utc: datetime, bbox: dict[str, float], variables: list[str], levels: list[str]) -> FetchResult:
        for date_str, cycle_hour in candidate_cycles(target_dt_utc):
            cycle_dt = datetime.strptime(f"{date_str}{cycle_hour:02d}", "%Y%m%d%H").replace(tzinfo=timezone.utc)
            fhr = clamp_forecast_hour(nearest_forecast_hour(target_dt_utc, cycle_dt))
            url = self.build_filter_url(date_str, cycle_hour, fhr, bbox, variables, levels)
            cache_key = f"{date_str}:{cycle_hour}:{fhr}:{json.dumps(self.normalize_bbox_for_nomads(bbox), sort_keys=True)}:{','.join(sorted(variables))}:{','.join(sorted(levels))}"
            path = self._cache_path(cache_key)
            if path.exists() and (time.time() - path.stat().st_mtime) < DEFAULT_GFS_CACHE_TTL_SECONDS:
                return FetchResult(True, path=path, cycle=f"{date_str}{cycle_hour:02d}", forecast_hour=fhr, valid_time=(cycle_dt + timedelta(hours=fhr)).isoformat(), url=url)
            try:
                self.fetch_subset(url, path)
                return FetchResult(True, path=path, cycle=f"{date_str}{cycle_hour:02d}", forecast_hour=fhr, valid_time=(cycle_dt + timedelta(hours=fhr)).isoformat(), url=url)
            except Exception as exc:
                continue
        return FetchResult(False, error="no available nomads subset")


CLOUD_REGIMES = {
    "marine_stratocumulus": {
        "deck_bias": 0.92,
        "tower_bias": 0.10,
        "wispy_bias": 0.08,
        "base_altitude_m": 700.0,
        "depth_m": 1400.0,
        "lateral_scale_km": 160.0,
        "underside_darkness": 0.22,
        "fringe_softness": 0.82,
    },
    "cumulus_field": {
        "deck_bias": 0.38,
        "tower_bias": 0.42,
        "wispy_bias": 0.12,
        "base_altitude_m": 1100.0,
        "depth_m": 2200.0,
        "lateral_scale_km": 95.0,
        "underside_darkness": 0.28,
        "fringe_softness": 0.58,
    },
    "frontal_shield": {
        "deck_bias": 0.78,
        "tower_bias": 0.24,
        "wispy_bias": 0.26,
        "base_altitude_m": 1200.0,
        "depth_m": 4200.0,
        "lateral_scale_km": 210.0,
        "underside_darkness": 0.34,
        "fringe_softness": 0.70,
    },
    "deep_convection": {
        "deck_bias": 0.18,
        "tower_bias": 0.96,
        "wispy_bias": 0.10,
        "base_altitude_m": 900.0,
        "depth_m": 9200.0,
        "lateral_scale_km": 120.0,
        "underside_darkness": 0.58,
        "fringe_softness": 0.36,
    },
    "cirrus_sheet": {
        "deck_bias": 0.20,
        "tower_bias": 0.06,
        "wispy_bias": 0.94,
        "base_altitude_m": 7600.0,
        "depth_m": 2400.0,
        "lateral_scale_km": 240.0,
        "underside_darkness": 0.14,
        "fringe_softness": 0.90,
    },
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def stable_hash_u32(text: str) -> int:
    h = hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]
    return int(h, 16)


def stable_unit_float(key: str) -> float:
    return (stable_hash_u32(key) % 1_000_000) / 1_000_000.0


def stable_range(key: str, lo: float, hi: float) -> float:
    return lo + (hi - lo) * stable_unit_float(key)


def close_ring(points: list[dict]) -> list[dict]:
    if not points:
        return []
    out = [dict(p) for p in points]
    if out[0].get("lat") != out[-1].get("lat") or out[0].get("lng") != out[-1].get("lng"):
        out.append({"lat": out[0]["lat"], "lng": out[0]["lng"]})
    return out


def ring_centroid(points: list[dict]) -> tuple[float, float]:
    if not points:
        return 0.0, 0.0
    core = points[:-1] if len(points) > 2 and points[0] == points[-1] else points
    lat = sum(float(p["lat"]) for p in core) / max(1, len(core))
    lon = sum(float(p["lng"]) for p in core) / max(1, len(core))
    return lat, lon


def scale_ring(points: list[dict], scale_lat: float, scale_lon: float, center_lat: float, center_lon: float) -> list[dict]:
    out = []
    for p in points:
        out.append(
            {
                "lat": center_lat + (float(p["lat"]) - center_lat) * scale_lat,
                "lng": center_lon + (float(p["lng"]) - center_lon) * scale_lon,
            }
        )
    return close_ring(out)


def offset_ring(points: list[dict], dlat: float, dlon: float) -> list[dict]:
    out = [{"lat": float(p["lat"]) + dlat, "lng": float(p["lng"]) + dlon} for p in points]
    return close_ring(out)


def jitter_ring(points: list[dict], key_base: str, lat_jitter: float, lon_jitter: float) -> list[dict]:
    out = []
    for i, p in enumerate(points):
        out.append(
            {
                "lat": float(p["lat"]) + stable_range(f"{key_base}:jlat:{i}", -lat_jitter, lat_jitter),
                "lng": float(p["lng"]) + stable_range(f"{key_base}:jlon:{i}", -lon_jitter, lon_jitter),
            }
        )
    return close_ring(out)


def km_to_lat_deg(km: float) -> float:
    return km / 110.574


def km_to_lon_deg(km: float, lat_deg: float) -> float:
    return km / max(10.0, 111.320 * math.cos(math.radians(lat_deg)))


def build_irregular_ellipse_ring(center_lat: float, center_lon: float, radius_lat_deg: float, radius_lon_deg: float, points_count: int, key_base: str, irregularity: float = 0.18) -> list[dict]:
    pts: list[dict] = []
    count = max(8, points_count)
    for i in range(count):
        t = (2 * math.pi * i) / count
        wobble = 1 + stable_range(f"{key_base}:w:{i}", -irregularity, irregularity)
        lat = center_lat + math.sin(t) * radius_lat_deg * wobble
        lng = center_lon + math.cos(t) * radius_lon_deg * wobble
        pts.append({"lat": round(lat, 6), "lng": round(lng, 6)})
    return close_ring(pts)


def build_hole_rings_for_cloud(center_lat: float, center_lon: float, outer_ring: list[dict], hole_count: int, key_base: str) -> list[list[dict]]:
    holes: list[list[dict]] = []
    c_lat, c_lon = ring_centroid(outer_ring)
    for i in range(max(0, hole_count)):
        s_lat = stable_range(f"{key_base}:hs:{i}:lat", 0.18, 0.42)
        s_lon = stable_range(f"{key_base}:hs:{i}:lon", 0.18, 0.42)
        ring = scale_ring(outer_ring, s_lat, s_lon, c_lat, c_lon)
        ring = offset_ring(ring, stable_range(f"{key_base}:ho:{i}:lat", -0.05, 0.05), stable_range(f"{key_base}:ho:{i}:lon", -0.05, 0.05))
        holes.append(ring)
    return holes


def build_subcell_layout(regime: str, density: float, organization: float, key_base: str) -> list[dict]:
    base = 3 + int(density * 4)
    if regime == "deep_convection":
        base += 3
    elif regime in {"marine_stratocumulus", "frontal_shield"}:
        base += 2
    count = max(3, min(10, base))
    out: list[dict] = []
    for i in range(count):
        role = "fringe"
        if i == 0:
            role = "core"
        if regime == "deep_convection" and i in {0, 1}:
            role = "tower" if i == 1 else "core"
        elif regime == "cirrus_sheet":
            role = "wispy"
        elif regime == "marine_stratocumulus" and i > count - 3:
            role = "deck"
        out.append(
            {
                "id": f"sc-{i}",
                "dx": round(stable_range(f"{key_base}:dx:{i}", -0.38, 0.38), 4),
                "dy": round(stable_range(f"{key_base}:dy:{i}", -0.38, 0.38), 4),
                "weight": round(_clamp(stable_range(f"{key_base}:w:{i}", 0.45, 1.0) * (0.6 + organization * 0.4), 0.0, 1.0), 4),
                "role": role,
            }
        )
    return out


def rgba_string(r: int, g: int, b: int, a: float) -> str:
    return f"rgba({int(_clamp(r,0,255))},{int(_clamp(g,0,255))},{int(_clamp(b,0,255))},{_clamp(a,0,1):.3f})"


def build_band_footprints(tile_id: str, regime: str, band: str, lat: float, lon: float, lateral_scale_km: float, organization: float, fringe_softness: float, wind_shear: float, anvil_dir_deg: float, anvil_spread_km: float, density: float, coverage: float, key_base: str) -> dict:
    lat_deg = km_to_lat_deg(max(8.0, lateral_scale_km * (0.42 + coverage * 0.8)))
    lon_deg = km_to_lon_deg(max(10.0, lateral_scale_km * (0.52 + coverage * 0.9)), lat)
    points_count = int(_clamp(12 + density * 10 + (1 - organization) * 4, 10, 24))

    footprints = []
    holes = []
    base_ring = build_irregular_ellipse_ring(lat, lon, lat_deg, lon_deg, points_count, f"{key_base}:base", irregularity=0.12 + fringe_softness * 0.18)
    base_ring = jitter_ring(base_ring, f"{key_base}:basejit", lat_deg * 0.08, lon_deg * 0.08)
    footprints.append({"role": "deck" if regime in {"marine_stratocumulus", "frontal_shield"} else "core", "points": base_ring})

    if regime == "deep_convection":
        core = scale_ring(base_ring, 0.45 + organization * 0.2, 0.45 + organization * 0.2, lat, lon)
        footprints.append({"role": "core", "points": core})
        tower = scale_ring(base_ring, 0.28 + organization * 0.16, 0.28 + organization * 0.16, lat, lon)
        footprints.append({"role": "tower", "points": tower})
    elif regime == "cirrus_sheet":
        smear = scale_ring(base_ring, 0.8, 1.35 + wind_shear * 0.5, lat, lon)
        smear = offset_ring(smear, math.sin(math.radians(anvil_dir_deg)) * 0.04, math.cos(math.radians(anvil_dir_deg)) * 0.06)
        footprints.append({"role": "wispy", "points": smear})
    else:
        fringe = scale_ring(base_ring, 1.12 + fringe_softness * 0.18, 1.12 + fringe_softness * 0.18, lat, lon)
        footprints.append({"role": "fringe", "points": fringe})

    if regime in {"marine_stratocumulus", "frontal_shield"}:
        hc = int(_clamp(1 + fringe_softness * 2, 0, 3))
        hole_rings = build_hole_rings_for_cloud(lat, lon, base_ring, hc, f"{key_base}:holes")
        for i, hr in enumerate(hole_rings):
            holes.append({"role": "deck_hole", "points": hr, "id": f"h-{i}"})

    if regime == "deep_convection" and anvil_spread_km > 15:
        anvil_lat = km_to_lat_deg(anvil_spread_km * 0.28)
        anvil_lon = km_to_lon_deg(anvil_spread_km * 0.54, lat)
        anvil = build_irregular_ellipse_ring(lat, lon, anvil_lat, anvil_lon, max(14, points_count), f"{key_base}:anvil", irregularity=0.24)
        footprints.append({"role": "anvil", "points": anvil})

    return {"footprints": footprints, "holes": holes}


def build_band_shells(regime: str, band: str, base_altitude_m: float, top_altitude_m: float, density: float, coverage: float, organization: float, key_base: str) -> list[dict]:
    _ = key_base
    depth = max(100.0, top_altitude_m - base_altitude_m)
    alpha = _clamp(0.12 + density * 0.26 + coverage * 0.18, 0.08, 0.62)
    z = 10 if band == "low" else 20 if band == "mid" else 30
    shells: list[dict] = []
    shells.append(
        {
            "role": "deck" if regime in {"marine_stratocumulus", "frontal_shield"} else "core",
            "footprint_ref": 0,
            "hole_refs": [0] if regime in {"marine_stratocumulus", "frontal_shield"} else [],
            "base_m": round(base_altitude_m, 1),
            "top_m": round(base_altitude_m + depth * (0.5 + organization * 0.35), 1),
            "fill": rgba_string(232, 238, 245, alpha),
            "stroke": rgba_string(245, 248, 252, alpha * 0.45),
            "stroke_width": 0.6,
            "extruded": regime != "cirrus_sheet",
            "z_index": z,
        }
    )
    if regime == "deep_convection":
        shells.append({"role": "tower", "footprint_ref": 2 if band != "high" else 1, "hole_refs": [], "base_m": round(base_altitude_m + depth * 0.25, 1), "top_m": round(top_altitude_m, 1), "fill": rgba_string(245, 247, 250, _clamp(alpha + 0.08, 0, 0.75)), "stroke": rgba_string(252, 253, 255, 0.22), "stroke_width": 0.7, "extruded": True, "z_index": z + 2})
        if band == "high":
            shells.append({"role": "anvil", "footprint_ref": min(3, 2), "hole_refs": [], "base_m": round(base_altitude_m + depth * 0.62, 1), "top_m": round(top_altitude_m, 1), "fill": rgba_string(238, 244, 252, _clamp(alpha * 0.8, 0.1, 0.5)), "stroke": rgba_string(246, 250, 255, 0.18), "stroke_width": 0.5, "extruded": True, "z_index": z + 3})
    elif regime == "cirrus_sheet":
        shells.append({"role": "wispy", "footprint_ref": 1, "hole_refs": [], "base_m": round(base_altitude_m + depth * 0.55, 1), "top_m": round(top_altitude_m, 1), "fill": rgba_string(225, 236, 250, _clamp(alpha * 0.65, 0.08, 0.38)), "stroke": rgba_string(239, 246, 255, 0.12), "stroke_width": 0.4, "extruded": False, "z_index": z + 1})
    else:
        shells.append({"role": "fringe", "footprint_ref": 1, "hole_refs": [], "base_m": round(base_altitude_m + depth * 0.18, 1), "top_m": round(base_altitude_m + depth * 0.72, 1), "fill": rgba_string(236, 242, 248, _clamp(alpha * 0.78, 0.08, 0.48)), "stroke": rgba_string(246, 250, 255, 0.14), "stroke_width": 0.5, "extruded": True, "z_index": z + 1})
    return shells


def classify_cloud_regime(tile: dict) -> str:
    low = float(tile.get("low_density") or 0)
    mid = float(tile.get("mid_density") or 0)
    high = float(tile.get("high_density") or 0)
    precip = float(tile.get("precipitation_factor") or 0)
    convection = float(tile.get("convection_factor") or 0)
    lat = float(((tile.get("bounds") or {}).get("lat_center") or 0))
    if convection > 0.68 and precip > 0.48:
        return "deep_convection"
    if high > 0.62 and low < 0.35 and precip < 0.38:
        return "cirrus_sheet"
    if low > 0.64 and mid < 0.5 and abs(lat) <= 44:
        return "marine_stratocumulus"
    if (low + mid + high) / 3 > 0.5 and mid > 0.46:
        return "frontal_shield"
    return "cumulus_field"


def compute_cloud_appearance(tile: dict, regime: str) -> dict:
    low = float(tile.get("low_density") or 0)
    mid = float(tile.get("mid_density") or 0)
    high = float(tile.get("high_density") or 0)
    precip = float(tile.get("precipitation_factor") or 0)
    convection = float(tile.get("convection_factor") or 0)
    wind = tile.get("wind") or {}
    u_low = float(((wind.get("low") or {}).get("u") or 0))
    v_low = float(((wind.get("low") or {}).get("v") or 0))
    u_high = float(((wind.get("high") or {}).get("u") or 0))
    v_high = float(((wind.get("high") or {}).get("v") or 0))
    wind_shear = _clamp(math.hypot(u_high - u_low, v_high - v_low) / 35.0, 0.0, 1.0)
    coverage = _clamp(low * 0.4 + mid * 0.35 + high * 0.25 + precip * 0.12, 0.0, 1.0)
    cfg = CLOUD_REGIMES.get(regime, CLOUD_REGIMES["cumulus_field"])
    anvil_dir = (math.degrees(math.atan2((u_low + u_high) / 2.0, (v_low + v_high) / 2.0)) + 360.0) % 360.0
    anvil_spread = 0.0
    if regime == "deep_convection":
        anvil_spread = _lerp(35.0, 140.0, _clamp(convection * 0.75 + wind_shear * 0.25, 0.0, 1.0))
    elif regime in {"frontal_shield", "cirrus_sheet"}:
        anvil_spread = _lerp(18.0, 95.0, _clamp(high * 0.7 + wind_shear * 0.3, 0.0, 1.0))
    organization = _clamp(0.24 + coverage * 0.38 + precip * 0.16 + convection * 0.22, 0.0, 1.0)
    return {
        "opacity": round(_clamp(0.16 + coverage * 0.62 + convection * 0.1, 0.0, 1.0), 4),
        "underside_darkness": round(_clamp(cfg["underside_darkness"] + precip * 0.16 + convection * 0.14, 0.0, 1.0), 4),
        "fringe_softness": round(_clamp(cfg["fringe_softness"] - convection * 0.1 + high * 0.08, 0.0, 1.0), 4),
        "organization": round(organization, 4),
        "wind_shear": round(wind_shear, 4),
        "tower_bias": round(_clamp(cfg["tower_bias"] + convection * 0.2, 0.0, 1.0), 4),
        "deck_bias": round(_clamp(cfg["deck_bias"] + low * 0.08 - convection * 0.1, 0.0, 1.0), 4),
        "wispy_bias": round(_clamp(cfg["wispy_bias"] + high * 0.1, 0.0, 1.0), 4),
        "anvil_dir_deg": round(anvil_dir, 2),
        "anvil_spread_km": round(anvil_spread, 1),
    }


def compute_cloud_importance(tile: dict, regime: str) -> float:
    low = float(tile.get("low_density") or 0)
    mid = float(tile.get("mid_density") or 0)
    high = float(tile.get("high_density") or 0)
    precip = float(tile.get("precipitation_factor") or 0)
    convection = float(tile.get("convection_factor") or 0)
    depth = float(tile.get("vertical_depth_m") or 2800)
    org = float(tile.get("organization") or 0.45)
    regime_boost = 0.08 if regime == "deep_convection" else 0.04 if regime in {"frontal_shield", "cirrus_sheet"} else 0.0
    score = low * 0.2 + mid * 0.24 + high * 0.18 + precip * 0.18 + convection * 0.18 + _clamp(depth / 12000.0, 0.0, 1.0) * 0.08 + org * 0.08 + regime_boost
    return round(_clamp(score, 0.0, 1.0), 4)


def enrich_cloud_band_geometry(tile: dict, band: str, regime: str, appearance: dict) -> dict:
    density = float(((tile.get("bands") or {}).get(band, {}).get("density") or tile.get(f"{band}_density") or 0))
    old_alt = float(tile.get(f"altitude_{band}") or (9000 if band == "high" else 4200 if band == "mid" else 1200))
    base_m = float(((tile.get("bands") or {}).get(band, {}).get("base_altitude_m") or old_alt))
    top_m = float(((tile.get("bands") or {}).get(band, {}).get("top_altitude_m") or (base_m + (1800 if band != "high" else 2200))))
    thickness = max(120.0, top_m - base_m)
    cov = float(((tile.get("bands") or {}).get(band, {}).get("coverage") or _clamp(density * 0.9 + float(tile.get("precipitation_factor") or 0) * 0.18, 0, 1)))
    lat = float(((tile.get("bounds") or {}).get("lat_center") or 0.0))
    lon = float(((tile.get("bounds") or {}).get("lon_center") or 0.0))
    lateral = float(((tile.get("bands") or {}).get(band, {}).get("lateral_scale_km") or _lerp(65.0, 190.0, _clamp(cov + density * 0.4, 0, 1))))
    key_base = f"{tile.get('tile_id','tile')}:{tile.get('seed','s')}:{regime}:{band}"
    footprints = build_band_footprints(
        tile_id=str(tile.get("tile_id") or "tile"),
        regime=regime,
        band=band,
        lat=lat,
        lon=lon,
        lateral_scale_km=lateral,
        organization=float(appearance.get("organization") or 0.5),
        fringe_softness=float(appearance.get("fringe_softness") or 0.6),
        wind_shear=float(appearance.get("wind_shear") or 0.0),
        anvil_dir_deg=float(appearance.get("anvil_dir_deg") or 0.0),
        anvil_spread_km=float(appearance.get("anvil_spread_km") or 0.0),
        density=density,
        coverage=cov,
        key_base=key_base,
    )
    shells = build_band_shells(
        regime=regime,
        band=band,
        base_altitude_m=base_m,
        top_altitude_m=top_m,
        density=density,
        coverage=cov,
        organization=float(appearance.get("organization") or 0.5),
        key_base=key_base,
    )
    wind = (((tile.get("wind") or {}).get(band)) or {})
    return {
        "density": round(density, 4),
        "coverage": round(cov, 4),
        "base_altitude_m": round(base_m, 1),
        "top_altitude_m": round(top_m, 1),
        "thickness_m": round(thickness, 1),
        "lateral_scale_km": round(lateral, 1),
        "wind": {"u": round(float(wind.get("u") or 0.0), 3), "v": round(float(wind.get("v") or 0.0), 3)},
        "footprints": footprints["footprints"],
        "holes": footprints["holes"],
        "shells": shells,
    }


def enrich_cloud_tile_geometry(tile: dict) -> dict:
    out = dict(tile)
    regime = classify_cloud_regime(out)
    appearance = compute_cloud_appearance(out, regime)
    seed_text = f"{out.get('tile_id','tile')}:{int(float(out.get('updated_at') or 0)//3600000)}:{regime}"
    out["seed"] = seed_text
    out["regime"] = regime
    out.update(appearance)
    out["importance"] = compute_cloud_importance(out, regime)
    bands = {}
    for b in ("low", "mid", "high"):
        bands[b] = enrich_cloud_band_geometry(out, b, regime, appearance)
    out["bands"] = bands
    out["subcells"] = build_subcell_layout(regime, max(float(out.get("low_density") or 0), float(out.get("mid_density") or 0), float(out.get("high_density") or 0)), float(appearance.get("organization") or 0.5), f"{seed_text}:sub")

    out["base_altitude_m"] = round(min(bands["low"]["base_altitude_m"], bands["mid"]["base_altitude_m"], bands["high"]["base_altitude_m"]), 1)
    out["top_altitude_m"] = round(max(bands["low"]["top_altitude_m"], bands["mid"]["top_altitude_m"], bands["high"]["top_altitude_m"]), 1)
    out["vertical_depth_m"] = round(max(0.0, out["top_altitude_m"] - out["base_altitude_m"]), 1)

    coverage = (float(bands["low"].get("coverage") or 0.0) * 0.42 + float(bands["mid"].get("coverage") or 0.0) * 0.36 + float(bands["high"].get("coverage") or 0.0) * 0.22)
    density = (float(bands["low"].get("density") or 0.0) * 0.40 + float(bands["mid"].get("density") or 0.0) * 0.36 + float(bands["high"].get("density") or 0.0) * 0.24)
    precip_factor = float(out.get("precipitation_factor") or 0.0)
    conv_factor = float(out.get("convection_factor") or 0.0)
    mid_wind = (out.get("wind") or {}).get("mid") or {}

    out["coverage"] = round(_clamp(coverage, 0.0, 1.0), 4)
    out["density"] = round(_clamp(density, 0.0, 1.0), 4)
    out["precip_rate"] = round(max(0.0, precip_factor * 45.0), 3)
    out["storm_energy"] = round(_clamp(conv_factor * 0.68 + precip_factor * 0.32, 0.0, 1.0), 4)
    out["wind_u"] = round(float(mid_wind.get("u") or 0.0), 3)
    out["wind_v"] = round(float(mid_wind.get("v") or 0.0), 3)
    out["importance"] = round(_clamp(float(out.get("importance") or 0.0), 0.0, 1.0), 4)

    # Explicitly mark these as estimated visualization fields.
    out["estimated_cloud_base_m"] = out["base_altitude_m"]
    out["estimated_cloud_top_m"] = out["top_altitude_m"]
    out["estimated_cloud_thickness_m"] = out["vertical_depth_m"]
    out["estimated_density"] = out["density"]

    return out


class GFSService:
    """Modular, namespaced GFS helpers for /gfs routes."""

    def __init__(self, static_dir: str) -> None:
        self.static_dir = Path(static_dir).resolve()
        self.data_dir = self.static_dir / "data"
        self.fishvid_dir = self.static_dir / "fishvid"
        self.store_path = self.data_dir / "gfs_location_store.json"
        self.state = GFSState()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.fishvid_dir.mkdir(parents=True, exist_ok=True)
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": DEFAULT_UA, "Accept": "application/json"})
        self.env_cache: Dict[str, Dict[str, Any]] = {}
        self.station_cache: Dict[str, Dict[str, Any]] = {}
        self.point_forecast_cache: Dict[str, Dict[str, Any]] = {}
        self.gfs_client = GFSNomadsClient(DEFAULT_GFS_CACHE_DIR)
        self.disk_cache = DiskCache(str(DEFAULT_GFS_CACHE_DIR / "payloads")) if DiskCache else None
        self._ingest_lock = threading.Lock()
        self._decode_cache: Dict[str, Dict[str, Any]] = {}

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _default_bbox(self) -> dict[str, float]:
        return {"west": -180.0, "south": -80.0, "east": 180.0, "north": 80.0}

    def _normalize_bbox(self, bbox: dict[str, float] | None = None) -> dict[str, float]:
        raw = bbox or self._default_bbox()
        return {
            "west": float(raw.get("west", -180.0)),
            "south": float(raw.get("south", -80.0)),
            "east": float(raw.get("east", 180.0)),
            "north": float(raw.get("north", 80.0)),
        }

    def _annotate_weather_payload(
        self,
        payload: dict[str, Any],
        *,
        bbox: dict[str, float],
        source: str,
        payload_state: str,
        heuristic: bool,
        quality_note: str,
        confidence: str,
    ) -> dict[str, Any]:
        out = dict(payload or {})
        out.setdefault("source", source)
        out.setdefault("cycle", None)
        out.setdefault("forecast_hour", None)
        out.setdefault("valid_time", None)
        out["bbox_used"] = bbox
        out["payload_state"] = payload_state
        out["heuristic"] = bool(heuristic)
        out["quality_note"] = quality_note
        out["confidence"] = confidence
        return out

    def _fish_csv_path(self) -> Path:
        if self.static_dir == STATIC_DIR.resolve():
            return FISH_CSV
        return self.data_dir / "fishloclist.csv"

    def _normalize_location_key(self, value: str) -> str:
        raw = (value or "").strip().lower()
        raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
        raw = re.sub(r"-{2,}", "-", raw).strip("-")
        return raw[:80]

    def _cache_get(self, cache: Dict[str, Dict[str, Any]], key: str, ttl_seconds: int) -> Any:
        row = cache.get(key)
        if not row:
            return None
        if (self._utc_now() - row["ts"]).total_seconds() > ttl_seconds:
            cache.pop(key, None)
            return None
        return row.get("value")

    def _cache_put(self, cache: Dict[str, Dict[str, Any]], key: str, value: Any) -> None:
        cache[key] = {"ts": self._utc_now(), "value": value}

    def _rounded_env_key(self, lat: float, lon: float, precision: int = 2) -> str:
        return f"{round(float(lat), precision)}:{round(float(lon), precision)}"

    def _http_json(self, url: str, params: Dict[str, Any] | None = None, timeout: float = DEFAULT_HTTP_TIMEOUT) -> Any:
        resp = self.http.get(url, params=params or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _safe_http_json(self, url: str, params: Dict[str, Any] | None = None, timeout: float = DEFAULT_HTTP_TIMEOUT) -> Any:
        try:
            return self._http_json(url, params=params, timeout=timeout)
        except Exception:
            return None

    def safe_data_var(self, ds: Any, names: list[str]) -> Any:
        """Return first matching data var from dataset by candidate names."""
        if ds is None:
            return None
        for name in names:
            if getattr(ds, "data_vars", None) is not None and name in ds.data_vars:
                return ds[name]
        return None

    def squeeze_forecast_array(self, arr: Any) -> Any:
        """Squeeze time/step dimensions to 2D spatial array."""
        if arr is None:
            return None
        a = arr
        for dim in ["time", "step", "valid_time", "isobaricInhPa", "heightAboveGround", "surface"]:
            if hasattr(a, "dims") and dim in a.dims and a.sizes.get(dim, 0) > 0:
                a = a.isel({dim: 0})
        return a

    def ensure_lat_lon_2d(self, ds: Any) -> tuple[Any, Any]:
        """Return 2D lat/lon arrays from dataset coords."""
        if ds is None or np is None:
            return None, None
        lat = ds.coords.get("latitude")
        if lat is None:
            lat = ds.coords.get("lat")
        lon = ds.coords.get("longitude")
        if lon is None:
            lon = ds.coords.get("lon")
        if lat is None or lon is None:
            return None, None
        latv = np.asarray(lat.values)
        lonv = np.asarray(lon.values)
        if latv.ndim == 1 and lonv.ndim == 1:
            lon2d, lat2d = np.meshgrid(lonv, latv)
            return lat2d, lon2d
        return latv, lonv

    def flip_lat_if_needed(self, arr: Any, lat: Any) -> Any:
        if np is None or arr is None or lat is None:
            return arr
        try:
            if lat.ndim >= 1 and lat[0, 0] < lat[-1, 0]:
                return np.flipud(arr)
        except Exception:
            pass
        return arr

    def to_native_float(self, value: Any) -> float:
        try:
            return float(value)
        except Exception:
            return 0.0

    def open_surface_dataset(self, grib_path: Path) -> Any:
        if xr is None:
            return None
        return xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface", "stepType": "instant"}, "indexpath": ""})

    def open_2m_dataset(self, grib_path: Path) -> Any:
        if xr is None:
            return None
        return xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}, "indexpath": ""})

    def open_10m_dataset(self, grib_path: Path) -> Any:
        if xr is None:
            return None
        return xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}, "indexpath": ""})

    def open_isobaric_dataset(self, grib_path: Path) -> Any:
        if xr is None:
            return None
        return xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "indexpath": ""})

    def open_mean_sea_dataset(self, grib_path: Path) -> Any:
        if xr is None:
            return None
        return xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea"}, "indexpath": ""})

    def _open_all_valid_groups_cfgrib(self, grib_path: Path) -> dict[str, Any]:
        groups: dict[str, Any] = {}
        openers = {
            "surface": self.open_surface_dataset,
            "2m": self.open_2m_dataset,
            "10m": self.open_10m_dataset,
            "isobaricInhPa": self.open_isobaric_dataset,
            "meanSea": self.open_mean_sea_dataset,
        }
        for name, fn in openers.items():
            try:
                ds = fn(grib_path)
                if ds is not None and len(getattr(ds, "data_vars", {})) > 0:
                    groups[name] = ds
                    print(f"[gfs] cfgrib group opened: {name} vars={list(ds.data_vars.keys())[:8]}")
                elif ds is not None:
                    print(f"[gfs] cfgrib group empty: {name}")
            except Exception as exc:
                print(f"[gfs] cfgrib group failed: {name}: {exc}")
        return groups

    def _open_all_valid_groups_pygrib(self, grib_path: Path) -> dict[str, Any]:
        """Fallback decoder path that normalizes pygrib messages to xarray datasets."""
        if pygrib is None or xr is None:
            return {}
        groups: dict[str, dict[str, Any]] = {}
        try:
            with pygrib.open(str(grib_path)) as grbs:
                for msg in grbs:
                    level_type = str(getattr(msg, "typeOfLevel", "") or "")
                    if level_type not in {"surface", "heightAboveGround", "isobaricInhPa", "meanSea"}:
                        continue
                    short_name = str(getattr(msg, "shortName", "") or "")
                    if not short_name:
                        continue
                    lat2d, lon2d = msg.latlons()
                    values = msg.values
                    if np is None:
                        continue
                    lat_arr = np.asarray(lat2d[:, 0], dtype=float)
                    lon_arr = np.asarray(lon2d[0, :], dtype=float)
                    val_arr = np.asarray(values, dtype=float)
                    if val_arr.ndim != 2:
                        continue
                    by_group = groups.setdefault(level_type, {})
                    if short_name in by_group:
                        continue
                    by_group[short_name] = xr.DataArray(val_arr, dims=("latitude", "longitude"), coords={"latitude": lat_arr, "longitude": lon_arr})
            out: dict[str, Any] = {}
            for gname, vars_map in groups.items():
                if vars_map:
                    out[gname] = xr.Dataset(vars_map)
                    print(f"[gfs] pygrib group opened: {gname} vars={list(vars_map.keys())[:8]}")
            return out
        except Exception as exc:
            print(f"[gfs] pygrib fallback failed: {exc}")
            return {}

    def open_all_valid_groups(self, grib_path: Path) -> tuple[dict[str, Any], str]:
        """Open available GRIB groups with cfgrib primary and pygrib fallback."""
        try:
            stat = grib_path.stat()
            cache_key = f"{grib_path}:{int(stat.st_mtime)}:{int(stat.st_size)}"
        except Exception:
            cache_key = str(grib_path)
        now_ms = self._now_ms()
        row = self._decode_cache.get(cache_key)
        if row and (now_ms - int(row.get("ts") or 0)) <= 1_000:
            return row.get("groups") or {}, str(row.get("backend") or "none")

        groups = self._open_all_valid_groups_cfgrib(grib_path)
        if groups:
            loaded = ", ".join([k for k in ["surface", "2m", "10m", "isobaric", "meanSea"] if (k in groups or (k == "isobaric" and "isobaricInhPa" in groups))])
            print(f"[gfs] datasets loaded: {loaded}")
            self._decode_cache = {cache_key: {"ts": now_ms, "groups": groups, "backend": "cfgrib"}}
            return groups, "cfgrib"
        groups = self._open_all_valid_groups_pygrib(grib_path)
        if groups:
            self._decode_cache = {cache_key: {"ts": now_ms, "groups": groups, "backend": "pygrib"}}
            return groups, "pygrib"
        self._decode_cache = {cache_key: {"ts": now_ms, "groups": {}, "backend": "none"}}
        return {}, "none"



    def _release_groups(self, groups: dict[str, Any] | None) -> None:
        for ds in (groups or {}).values():
            close_fn = getattr(ds, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass

    def _model_analysis_time_from_cycle(self, cycle: str | None) -> str | None:
        if not cycle:
            return None
        try:
            return datetime.strptime(str(cycle), "%Y%m%d%H").replace(tzinfo=timezone.utc).isoformat()
        except Exception:
            return None

    def _collect_available_fields(self, groups: dict[str, Any]) -> tuple[list[str], list[str]]:
        available = set()
        for gname, ds in (groups or {}).items():
            for v in list(getattr(ds, "data_vars", {}).keys()):
                available.add(f"{gname}:{v}")

        desired = {
            "surface:PRATE", "surface:APCP", "surface:TCDC", "surface:CAPE", "surface:UGRD", "surface:VGRD",
            "2m:TCDC", "2m:UGRD", "2m:VGRD", "10m:UGRD", "10m:VGRD",
            "isobaricInhPa:RH", "isobaricInhPa:TMP", "isobaricInhPa:HGT", "isobaricInhPa:UGRD", "isobaricInhPa:VGRD",
        }
        missing = sorted([k for k in desired if k not in available])
        return sorted(available), missing

    def _update_ingest_state_success(self, fetch: FetchResult, groups: dict[str, Any], mode: str, error: str | None = None) -> None:
        now_ms = self._now_ms()
        self.state.ingest_status = mode
        self.state.ingest_last_success_ts = now_ms if mode in {"live", "last_known_good"} else self.state.ingest_last_success_ts
        self.state.ingest_error = error
        self.state.model_cycle = fetch.cycle or self.state.model_cycle
        self.state.model_forecast_hour = fetch.forecast_hour if fetch.forecast_hour is not None else self.state.model_forecast_hour
        self.state.model_valid_time = fetch.valid_time or self.state.model_valid_time
        self.state.model_analysis_time = self._model_analysis_time_from_cycle(fetch.cycle) or self.state.model_analysis_time
        self.state.model_source_url = fetch.url or self.state.model_source_url
        self.state.model_cache_path = str(fetch.path) if fetch.path else self.state.model_cache_path
        self.state.degraded_mode = mode != "live"
        self.state.using_last_known_good = mode == "last_known_good"
        if self.state.decode_backend == "none":
            self.state.data_source_mode = "heuristic"
        elif self.state.decode_backend == "cfgrib":
            self.state.data_source_mode = "primary"
        else:
            self.state.data_source_mode = "fallback"
        available, missing = self._collect_available_fields(groups)
        self.state.fields_available = available
        self.state.fields_missing = missing

    def ingest_latest_model_fields(self, bbox: dict[str, float]) -> dict[str, Any]:
        """Attempt real NOMADS->GRIB2 ingestion and retain last-known-good state on failure."""
        bbox = self._normalize_bbox(bbox)
        now_ms = self._now_ms()
        self.state.ingest_last_attempt_ts = now_ms

        with self._ingest_lock:
            now = utc_now()
            ttl_ms = INGEST_MIN_INTERVAL_SECONDS * 1000
            recent_ok = (
                self.state.ingest_last_success_ts
                and (now_ms - int(self.state.ingest_last_success_ts)) < ttl_ms
                and self.state.last_good_model_state
            )
            if recent_ok:
                lkg = self.state.last_good_model_state or {}
                lkg_path_raw = lkg.get("fetch", {}).get("path")
                lkg_path = Path(lkg_path_raw) if lkg_path_raw else None
                if lkg_path and lkg_path.exists():
                    lkg_groups, lkg_backend = self.open_all_valid_groups(lkg_path)
                    if lkg_groups:
                        self.state.decode_backend = lkg_backend
                        self.state.data_source_mode = "primary" if lkg_backend == "cfgrib" else "fallback" if lkg_backend == "pygrib" else "heuristic"
                        lkg_fetch = FetchResult(
                            ok=True,
                            path=lkg_path,
                            cycle=str(lkg.get("fetch", {}).get("cycle") or ""),
                            forecast_hour=int(lkg.get("fetch", {}).get("forecast_hour") or 0),
                            valid_time=str(lkg.get("fetch", {}).get("valid_time") or ""),
                            error="",
                            url=str(lkg.get("fetch", {}).get("url") or ""),
                        )
                        self._update_ingest_state_success(lkg_fetch, lkg_groups, mode="last_known_good")
                        return {"mode": "last_known_good", "fetch": lkg_fetch, "groups": lkg_groups, "bbox": lkg.get("bbox") or bbox}
            try:
                fetch = self.gfs_client.fetch_latest_available_subset(now, bbox, DEFAULT_REQUIRED_VARIABLES, DEFAULT_REQUIRED_LEVELS)
                if not fetch.ok or not fetch.path:
                    raise RuntimeError(fetch.error or "nomads fetch failed")
                print(f"[gfs-ingest] selected cycle={fetch.cycle} fhr={fetch.forecast_hour} url={fetch.url}")
                if fetch.path.stat().st_size < INGEST_CACHE_MIN_BYTES:
                    raise RuntimeError("downloaded GRIB2 too small")
                print(f"[gfs-ingest] cache file ready path={fetch.path} size={fetch.path.stat().st_size}")

                try:
                    groups, decode_backend = self.open_all_valid_groups(fetch.path)
                except Exception as e:
                    print(f"[gfs] gfs decode failed: {e}")
                    raise RuntimeError("gfs decode failed") from e
                self.state.decode_backend = decode_backend
                self.state.data_source_mode = "primary" if decode_backend == "cfgrib" else "fallback" if decode_backend == "pygrib" else "heuristic"
                if not groups:
                    raise RuntimeError("no GRIB groups decoded")

                print(f"[gfs-ingest] decoded groups={list(groups.keys())} backend={decode_backend}")
                self.state.last_good_model_state = {
                    "fetch": {
                        "cycle": fetch.cycle,
                        "forecast_hour": fetch.forecast_hour,
                        "valid_time": fetch.valid_time,
                        "url": fetch.url,
                        "path": str(fetch.path),
                    },
                    "bbox": bbox,
                    "saved_at": now_ms,
                }
                self._update_ingest_state_success(fetch, groups, mode="live")
                return {"mode": "live", "fetch": fetch, "groups": groups, "bbox": bbox}
            except Exception as exc:
                print(f"[gfs-ingest] live ingest failed: {exc}")
                self.state.ingest_error = str(exc)
                lkg = self.state.last_good_model_state or {}
                lkg_path_raw = lkg.get("fetch", {}).get("path")
                lkg_path = Path(lkg_path_raw) if lkg_path_raw else None
                if lkg_path and lkg_path.exists():
                    lkg_groups, lkg_backend = self.open_all_valid_groups(lkg_path)
                    if lkg_groups:
                        self.state.decode_backend = lkg_backend
                        self.state.data_source_mode = "primary" if lkg_backend == "cfgrib" else "fallback" if lkg_backend == "pygrib" else "heuristic"
                        lkg_fetch = FetchResult(
                            ok=True,
                            path=lkg_path,
                            cycle=str(lkg.get("fetch", {}).get("cycle") or ""),
                            forecast_hour=int(lkg.get("fetch", {}).get("forecast_hour") or 0),
                            valid_time=str(lkg.get("fetch", {}).get("valid_time") or ""),
                            error="",
                            url=str(lkg.get("fetch", {}).get("url") or ""),
                        )
                        print("[gfs-ingest] using last-known-good GRIB2 file")
                        self._update_ingest_state_success(lkg_fetch, lkg_groups, mode="last_known_good", error=str(exc))
                        return {"mode": "last_known_good", "fetch": lkg_fetch, "groups": lkg_groups, "bbox": lkg.get("bbox") or bbox, "error": str(exc)}
                self.state.ingest_status = "failed"
                self.state.degraded_mode = True
                self.state.using_last_known_good = False
                self.state.decode_backend = "none"
                self.state.data_source_mode = "heuristic"
                raise

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _iso_utc(self, dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _time_of_day_bucket(self, lat: float, lon: float, ts_ms: int) -> str:
        _ = lat
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) + timedelta(hours=round(lon / 15.0))
        hour = dt.hour
        if 4 <= hour < 7:
            return "dawn"
        if 7 <= hour < 17:
            return "day"
        if 17 <= hour < 20:
            return "dusk"
        return "night"

    def _sun_angle_proxy(self, lat: float, lon: float, ts_ms: int) -> float:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        day_frac = (dt.hour + dt.minute / 60.0 + lon / 15.0) / 24.0
        seasonal = math.cos(2 * math.pi * ((dt.timetuple().tm_yday - 172) / 365.25))
        solar = math.sin(2 * math.pi * (day_frac - 0.25))
        lat_factor = math.cos(math.radians(lat))
        return _clamp((solar * lat_factor * 0.85 + seasonal * 0.15 + 1.0) / 2.0, 0.0, 1.0)

    def _moon_phase_proxy(self, ts_ms: int) -> Dict[str, Any]:
        days = ts_ms / 1000.0 / 86400.0
        synodic = 29.53058867
        phase = (days % synodic) / synodic
        illum = 0.5 * (1 - math.cos(2 * math.pi * phase))
        if phase < 0.03 or phase > 0.97:
            label = "new"
        elif 0.47 <= phase <= 0.53:
            label = "full"
        elif phase < 0.5:
            label = "waxing"
        else:
            label = "waning"
        return {"phase": round(phase, 4), "illumination": round(illum, 4), "label": label}

    BAIT_TERMS = ["anchovy", "sardine", "mackerel", "smelt", "herring", "bait ball", "boil", "chum", "squid", "shiner"]
    RIG_TERMS = ["carolina", "sabiki", "flyline", "float", "jig", "swimbait", "dropper loop", "texas rig", "spinner", "topwater"]
    SPECIES_TERMS = ["halibut", "calico", "bass", "yellowtail", "perch", "corbina", "tuna", "snapper", "tarpon", "snook"]

    def _extract_text_blobs(self, point: Dict[str, Any]) -> List[str]:
        blobs: List[str] = []
        for field in ("name", "location_key", "description", "notes", "intent", "area", "zone"):
            v = point.get(field)
            if isinstance(v, str) and v.strip():
                blobs.append(v.strip())

        meta = point.get("meta") if isinstance(point.get("meta"), dict) else {}
        for k, v in meta.items():
            if isinstance(v, str) and v.strip() and any(tok in k.lower() for tok in ("report", "note", "comment", "desc", "bait", "rig", "species")):
                blobs.append(v.strip())

        store = self._load_store()
        key = self._normalize_location_key(str(point.get("location_key") or ""))
        rec = ((store.get("locations") or {}).get(key) or {}) if key else {}
        report_text = rec.get("report_text")
        if isinstance(report_text, str) and report_text.strip():
            blobs.append(report_text.strip())
        return blobs

    def _term_counts(self, texts: List[str], terms: List[str]) -> Dict[str, int]:
        joined = "\n".join(texts).lower()
        out: Dict[str, int] = {}
        for t in terms:
            cnt = len(re.findall(rf"\b{re.escape(t.lower())}\b", joined))
            if cnt:
                out[t] = cnt
        return out

    def _history_features_for_point(self, point: Dict[str, Any]) -> Dict[str, Any]:
        texts = self._extract_text_blobs(point)
        bait_mentions = self._term_counts(texts, self.BAIT_TERMS)
        rig_mentions = self._term_counts(texts, self.RIG_TERMS)
        species_mentions = self._term_counts(texts, self.SPECIES_TERMS)
        report_count = len([t for t in texts if len(t) > 12])

        richness = _clamp((report_count / 8.0) + (sum(bait_mentions.values()) / 12.0), 0.0, 1.0)
        success = _clamp(0.25 + richness * 0.55 + (sum(species_mentions.values()) / 15.0), 0.0, 1.0)

        def top_terms(d: Dict[str, int]) -> List[str]:
            return [k for k, _ in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:3]]

        return {
            "report_count": report_count,
            "bait_mentions": bait_mentions,
            "rig_mentions": rig_mentions,
            "species_mentions": species_mentions,
            "historical_success_score": round(success, 4),
            "dominant_baits": top_terms(bait_mentions),
            "dominant_rigs": top_terms(rig_mentions),
            "dominant_species": top_terms(species_mentions),
        }

    def _supports_us_station_enrichment(self, lat: float, lon: float) -> bool:
        return (15.0 <= lat <= 72.5 and -170.0 <= lon <= -60.0)

    def _supports_global_gfs(self, lat: float, lon: float) -> bool:
        return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0

    def _point_has_report_history(self, point: Dict[str, Any]) -> bool:
        h = self._history_features_for_point(point)
        return h.get("report_count", 0) > 0 or bool(h.get("dominant_baits"))

    def _select_environment_strategy(self, lat: float, lon: float, point: Dict[str, Any]) -> str:
        if self._supports_us_station_enrichment(lat, lon):
            return "station_enriched_us"
        if self._supports_global_gfs(lat, lon):
            return "global_model_gfs"
        if self._point_has_report_history(point):
            return "history_augmented"
        return "heuristic_only"

    def _candidate_coops_stations(self, lat: float, lon: float) -> List[Dict[str, Any]]:
        _ = (lat, lon)
        return [
            {"id": "9410170", "name": "San Diego", "lat": 32.714, "lon": -117.173},
            {"id": "9414290", "name": "San Francisco", "lat": 37.806, "lon": -122.465},
            {"id": "8724580", "name": "Key West", "lat": 24.556, "lon": -81.807},
            {"id": "8518750", "name": "The Battery", "lat": 40.701, "lon": -74.014},
            {"id": "9455920", "name": "Anchorage", "lat": 61.238, "lon": -149.89},
        ]

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

    def _nearest_coops_station_id(self, lat: float, lon: float) -> str | None:
        stations = self._candidate_coops_stations(lat, lon)
        nearest = None
        best = 1e9
        for st in stations:
            d = self._haversine_km(lat, lon, float(st["lat"]), float(st["lon"]))
            if d < best:
                best = d
                nearest = st["id"]
        return nearest if best <= 900 else None

    def _fetch_nws_point_meta(self, lat: float, lon: float) -> Any:
        key = f"nws-point:{self._rounded_env_key(lat, lon, 2)}"
        cached = self._cache_get(self.point_forecast_cache, key, NWS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(f"{NWS_API_BASE}/points/{lat:.4f},{lon:.4f}")
        self._cache_put(self.point_forecast_cache, key, payload)
        return payload

    def _fetch_nws_hourly_forecast(self, lat: float, lon: float) -> Any:
        meta = self._fetch_nws_point_meta(lat, lon)
        hourly_url = (((meta or {}).get("properties") or {}).get("forecastHourly"))
        if not hourly_url:
            return None
        key = f"nws-hourly:{hourly_url}"
        cached = self._cache_get(self.point_forecast_cache, key, NWS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(hourly_url)
        self._cache_put(self.point_forecast_cache, key, payload)
        return payload

    def _parse_nws_hourly_environment(self, payload: Any) -> Dict[str, Any]:
        periods = (((payload or {}).get("properties") or {}).get("periods") or [])
        p0 = periods[0] if periods else {}
        wind_speed_val = str(p0.get("windSpeed") or "0")
        m = re.search(r"(\d+)", wind_speed_val)
        wind_mph = float(m.group(1)) if m else 0.0
        return {
            "short_forecast": p0.get("shortForecast"),
            "temperature_f": p0.get("temperature"),
            "wind_speed_kt": round(wind_mph * 0.868976, 2),
            "wind_direction_text": p0.get("windDirection"),
            "precip_probability_pct": (((p0.get("probabilityOfPrecipitation") or {}).get("value")) or 0),
            "relative_humidity_pct": (((p0.get("relativeHumidity") or {}).get("value")) or 0),
            "is_daytime": bool(p0.get("isDaytime", True)),
        }

    def _fetch_coops_tides(self, station_id: str) -> Any:
        key = f"coops-tide:{station_id}"
        cached = self._cache_get(self.station_cache, key, TIDE_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        now = self._utc_now()
        payload = self._safe_http_json(
            NOAA_TIDES_API,
            params={
                "product": "predictions",
                "application": "lftr",
                "station": station_id,
                "datum": "MLLW",
                "time_zone": "gmt",
                "units": "english",
                "interval": "h",
                "format": "json",
                "begin_date": now.strftime("%Y%m%d"),
                "end_date": (now + timedelta(days=1)).strftime("%Y%m%d"),
            },
        )
        self._cache_put(self.station_cache, key, payload)
        return payload

    def _fetch_coops_currents(self, station_id: str) -> Any:
        key = f"coops-current:{station_id}"
        cached = self._cache_get(self.station_cache, key, TIDE_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(
            NOAA_TIDES_API,
            params={
                "product": "currents_predictions",
                "application": "lftr",
                "station": station_id,
                "time_zone": "gmt",
                "units": "english",
                "interval": "MAX_SLACK",
                "format": "json",
            },
        )
        self._cache_put(self.station_cache, key, payload)
        return payload

    def _parse_tide_payload(self, payload: Any) -> Dict[str, Any]:
        preds = (payload or {}).get("predictions") or []
        if not preds:
            return {"tide_height_ft": None, "tide_time_utc": None}
        p0 = preds[0]
        height = p0.get("v")
        return {"tide_height_ft": float(height) if height is not None else None, "tide_time_utc": p0.get("t")}

    def _parse_currents_payload(self, payload: Any) -> Dict[str, Any]:
        arr = (payload or {}).get("current_predictions") or (payload or {}).get("cp") or []
        if not arr:
            return {"current_speed_kt": None, "current_direction_deg": None}
        p0 = arr[0]
        speed = p0.get("Velocity_Major") or p0.get("v") or p0.get("speed")
        direction = p0.get("Direction_Bin") or p0.get("d") or p0.get("direction")
        return {
            "current_speed_kt": float(speed) if speed not in (None, "") else None,
            "current_direction_deg": float(direction) if direction not in (None, "") else None,
        }

    def _gfsish_environment_proxy(self, lat: float, lon: float, ts_ms: int) -> Dict[str, Any]:
        hour_bucket = ts_ms // 3_600_000
        low, mid, high, precip, convection = self._cloud_density_triplet(lat, lon, int(hour_bucket))
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        t = hour_bucket / 5.0
        u = 6.0 + 8.5 * math.sin(lat_r * 0.8 - lon_r * 0.3 + t * 0.14)
        v = 2.6 * math.cos(lon_r * 0.85 + t * 0.09)
        speed_ms = math.hypot(u, v)
        wind_dir = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
        cloud_cover = _clamp(low * 0.52 + mid * 0.31 + high * 0.17, 0.0, 1.0)
        pressure = 1016.0 - precip * 14.0 - convection * 8.0 + (0.5 - cloud_cover) * 5.0
        return {
            "cloud_cover_pct": int(round(cloud_cover * 100)),
            "precipitation_factor": round(precip, 4),
            "convection_factor": round(convection, 4),
            "wind_speed_kt": round(speed_ms * 1.94384, 2),
            "wind_direction_deg": round(wind_dir, 1),
            "wind_gust_kt": round(speed_ms * 1.94384 * (1.2 + convection * 0.4), 2),
            "pressure_mb": round(pressure, 1),
        }

    def _fetch_sst_erddap(self, lat: float, lon: float) -> Dict[str, Any] | None:
        key = f"sst:{self._rounded_env_key(lat, lon, 1)}"
        cached = self._cache_get(self.env_cache, key, SST_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = None
        self._cache_put(self.env_cache, key, payload)
        return payload

    def _build_station_plus_gfs_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        nws = self._parse_nws_hourly_environment(self._fetch_nws_hourly_forecast(lat, lon))
        station_id = self._nearest_coops_station_id(lat, lon)
        tides = self._parse_tide_payload(self._fetch_coops_tides(station_id)) if station_id else {}
        currents = self._parse_currents_payload(self._fetch_coops_currents(station_id)) if station_id else {}
        moon = self._moon_phase_proxy(ts_ms)
        return {
            **env,
            **{k: v for k, v in nws.items() if v is not None},
            **{k: v for k, v in tides.items() if v is not None},
            **{k: v for k, v in currents.items() if v is not None},
            "station_id": station_id,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": moon,
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_global_gfs_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        sst = self._fetch_sst_erddap(lat, lon) or {}
        return {
            **env,
            **sst,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_history_augmented_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        history = self._history_features_for_point(point)
        return {
            **env,
            "history_success_score": history.get("historical_success_score", 0),
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_heuristic_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        return {
            **env,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_environment_meta(self, strategy: str, env: Dict[str, Any], point: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Any]:
        _ = (env, point)
        mod_map = {
            "station_enriched_us": 1.12,
            "global_model_gfs": 1.0,
            "history_augmented": 0.9,
            "heuristic_only": 0.78,
        }
        sources = ["solar_lunar", "history"]
        if strategy == "station_enriched_us":
            sources += ["global_gfs_proxy", "nws_hourly", "coops"]
        elif strategy == "global_model_gfs":
            sources += ["global_gfs_proxy", "sst_optional"]
        elif strategy == "history_augmented":
            sources += ["global_gfs_proxy", "report_history"]
        else:
            sources += ["global_gfs_proxy"]
        return {
            "source_tier": strategy,
            "sources_used": sources,
            "station_supported": strategy == "station_enriched_us",
            "global_supported": self._supports_global_gfs(float(point.get("lat") or 0), float(point.get("lon") or 0)),
            "history_supported": bool(history.get("report_count") or history.get("dominant_baits")),
            "coverage_mode": "worldwide_backbone",
            "confidence_modifier": mod_map.get(strategy, 0.78),
        }

    def _build_environment_context(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = f"env:{self._rounded_env_key(lat, lon)}:{int(ts_ms // 600000)}:{hashlib.md5(str(point.get('location_key','')).encode()).hexdigest()[:8]}"
        cached = self._cache_get(self.env_cache, cache_key, ENV_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

        history = self._history_features_for_point(point)
        strategy = self._select_environment_strategy(lat, lon, point)
        if strategy == "station_enriched_us":
            environment = self._build_station_plus_gfs_environment(lat, lon, ts_ms, point)
        elif strategy == "global_model_gfs":
            environment = self._build_global_gfs_environment(lat, lon, ts_ms, point)
        elif strategy == "history_augmented":
            environment = self._build_history_augmented_environment(lat, lon, ts_ms, point)
        else:
            environment = self._build_heuristic_environment(lat, lon, ts_ms, point)
        env_meta = self._build_environment_meta(strategy, environment, point, history)
        out = {"environment": environment, "environment_meta": env_meta, "history": history}
        self._cache_put(self.env_cache, cache_key, out)
        return out

    def _score_bait_theory(self, lat: float, lon: float, ts_ms: int, env: Dict[str, Any], history: Dict[str, Any], env_meta: Dict[str, Any]) -> Dict[str, Any]:
        tod = self._time_of_day_bucket(lat, lon, ts_ms)
        tod_score = {"dawn": 0.84, "day": 0.58, "dusk": 0.82, "night": 0.48}.get(tod, 0.55)
        sun = float(env.get("sun_angle") or self._sun_angle_proxy(lat, lon, ts_ms))
        moon = ((env.get("moon") or {}).get("illumination") if isinstance(env.get("moon"), dict) else None)
        moon = float(moon or self._moon_phase_proxy(ts_ms).get("illumination") or 0.5)
        wind = float(env.get("wind_speed_kt") or 8.0)
        cloud = float(env.get("cloud_cover_pct") or 45.0) / 100.0
        precip = float(env.get("precipitation_factor") or 0.1)
        convection = float(env.get("convection_factor") or 0.1)
        current = float(env.get("current_speed_kt") or 0.7)
        tide_height = float(env.get("tide_height_ft") or 1.6)
        hist = float(history.get("historical_success_score") or 0.35)
        conf_mod = float(env_meta.get("confidence_modifier") or 0.8)

        wind_pref = 1.0 - min(1.0, abs(wind - 11.0) / 20.0)
        cloud_pref = 1.0 - abs(cloud - 0.45)
        precip_pen = max(0.0, 1.0 - precip * 0.55)
        conv_pen = max(0.0, 1.0 - convection * 0.35)
        current_pref = 1.0 - min(1.0, abs(current - 1.4) / 2.2)
        tide_pref = 1.0 - min(1.0, abs(tide_height - 2.2) / 4.5)

        presence = _clamp(
            (tod_score * 0.22 + wind_pref * 0.14 + cloud_pref * 0.12 + current_pref * 0.13 + tide_pref * 0.08 + sun * 0.09 + moon * 0.04 + hist * 0.18)
            * precip_pen
            * conv_pen,
            0.03,
            0.98,
        )

        confidence = int(round(_clamp((0.44 + hist * 0.24 + wind_pref * 0.14 + cloud_pref * 0.08) * conf_mod, 0.12, 0.97) * 100))
        if presence >= 0.7:
            intensity = "high"
        elif presence >= 0.42:
            intensity = "medium"
        else:
            intensity = "low"

        dominant_baits = history.get("dominant_baits") or []
        bait_candidates = dominant_baits[:]
        if not bait_candidates:
            bait_candidates = ["anchovy", "sardine", "mackerel"] if cloud < 0.65 else ["sardine", "squid", "smelt"]

        dominant_rigs = history.get("dominant_rigs") or []
        best_rig = dominant_rigs[0] if dominant_rigs else ("flyline" if presence > 0.6 else "dropper loop")

        if presence > 0.72:
            school_size = "large, cohesive bait balls"
            depth = [8, 38]
            agg = "tight_ball"
            mobility = "aggressive_migration"
            line_class = "20-30 lb"
        elif presence > 0.45:
            school_size = "medium scattered schools"
            depth = [18, 62]
            agg = "broken_patches"
            mobility = "moderate_roaming"
            line_class = "15-25 lb"
        else:
            school_size = "small fragmented schools"
            depth = [35, 95]
            agg = "loose_columns"
            mobility = "slow_drift"
            line_class = "10-20 lb"

        feeding_label = {"dawn": "Prime dawn feed", "dusk": "Prime dusk feed", "day": "Intermittent daytime feed", "night": "Low-light nighttime pick"}.get(tod, "Active window")

        surface_boils = _clamp(presence * (1.1 - cloud * 0.3) * (1 - precip * 0.35), 0.0, 1.0)
        predator_pressure = _clamp(0.32 + presence * 0.55 + hist * 0.2, 0.0, 1.0)

        return {
            "intensity": intensity,
            "confidence": confidence,
            "presence_probability": round(presence, 4),
            "school_size_estimate": school_size,
            "school_depth_band_ft": depth,
            "bait_type_candidates": bait_candidates,
            "aggregation_mode": agg,
            "mobility": mobility,
            "feeding_window": {"label": feeding_label, "confidence": round(_clamp(0.45 + presence * 0.5, 0.0, 1.0), 4)},
            "surface_boils_likelihood": round(surface_boils, 4),
            "predator_pressure": round(predator_pressure, 4),
            "school_summary": f"{school_size} expected in {depth[0]}-{depth[1]} ft with {bait_candidates[0]} emphasis.",
            "recommendation": {
                "best_bait": bait_candidates[0],
                "best_depth_zone_ft": depth,
                "best_rig": best_rig,
                "best_line_class": line_class,
            },
        }

    def _build_bait_intel(self, point: Dict[str, Any], ts_ms: int) -> Dict[str, Any]:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            return self._heuristic_context(None, None, ts_ms)
        lat_f = float(lat)
        lon_f = float(lon)
        env_ctx = self._build_environment_context(lat_f, lon_f, ts_ms, point)
        bait = self._score_bait_theory(lat_f, lon_f, ts_ms, env_ctx["environment"], env_ctx["history"], env_ctx["environment_meta"])
        weather_summary = (
            f"Clouds {int(env_ctx['environment'].get('cloud_cover_pct', 0))}% | "
            f"Wind {env_ctx['environment'].get('wind_speed_kt', 0)} kt | "
            f"Pressure {env_ctx['environment'].get('pressure_mb', 'n/a')} mb"
        )
        water_context = (
            f"Current {env_ctx['environment'].get('current_speed_kt', 'n/a')} kt | "
            f"Tide {env_ctx['environment'].get('tide_height_ft', 'n/a')} ft | "
            f"Tier {env_ctx['environment_meta'].get('source_tier')}"
        )
        return {
            "bait": bait,
            "environment": env_ctx["environment"],
            "environment_meta": env_ctx["environment_meta"],
            "history": env_ctx["history"],
            "weather": {
                "summary": weather_summary,
                "water_context": water_context,
            },
        }

    def _heuristic_context(self, lat: float | None, lon: float | None, ts_ms: int) -> Dict[str, Any]:
        if lat is None or lon is None or not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
            fallback_point = {"lat": DEFAULT_WORLD_ENV_MARKER["lat"], "lon": DEFAULT_WORLD_ENV_MARKER["lon"], "location_key": "fallback", "meta": {}}
            intel = self._build_bait_intel(fallback_point, ts_ms)
            intel["bait"]["intensity"] = "low"
            intel["bait"]["confidence"] = min(intel["bait"].get("confidence", 42), 42)
            intel["environment_meta"]["source_tier"] = "heuristic_only"
            return intel

        point = {"lat": float(lat), "lon": float(lon), "location_key": f"pt-{self._rounded_env_key(float(lat), float(lon), 3)}", "meta": {}}
        return self._build_bait_intel(point, ts_ms)

    def _stable_noise(self, a: float, b: float, c: float) -> float:
        mix = math.sin(a * 12.9898 + b * 78.233 + c * 37.719)
        return mix - math.floor(mix)

    def _cloud_density_triplet(self, lat: float, lon: float, hour_bucket: int) -> Tuple[float, float, float, float, float]:
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        t = hour_bucket / 6.0

        macro = (
            0.5
            + 0.25 * math.sin(2.4 * lat_r + 0.8 * lon_r + t * 0.9)
            + 0.18 * math.cos(3.7 * lon_r - t * 0.7)
            + 0.12 * math.sin(5.2 * (lat_r + lon_r) + t * 0.35)
        )
        macro = max(0.0, min(1.0, macro))

        frontness = abs(math.sin(lat_r * 2.7 + lon_r * 0.35 + t * 0.45))
        gradient = abs(math.cos(lat_r * 3.9 - lon_r * 1.4 + t * 0.28))
        convective_proxy = max(0.0, min(1.0, 0.35 + 0.55 * frontness * gradient))

        humidity_surface = max(0.0, min(1.0, 0.45 + 0.35 * math.cos(lat_r - t * 0.08) + 0.2 * self._stable_noise(lat, lon, t)))
        humidity_mid = max(0.0, min(1.0, 0.4 + 0.42 * math.sin(lat_r * 1.6 + t * 0.1) + 0.18 * self._stable_noise(lat * 0.7, lon * 0.5, t)))
        humidity_high = max(0.0, min(1.0, 0.35 + 0.45 * math.cos(lon_r * 0.9 + t * 0.09) + 0.15 * self._stable_noise(lat * 0.6, lon * 0.3, t + 2)))

        low = max(0.0, min(1.0, macro * 0.7 + humidity_surface * 0.35 + convective_proxy * 0.2 - 0.12))
        mid = max(0.0, min(1.0, macro * 0.6 + humidity_mid * 0.45 + convective_proxy * 0.24 - 0.16))
        high = max(0.0, min(1.0, macro * 0.55 + humidity_high * 0.5 + frontness * 0.22 - 0.18))

        precipitation_factor = max(0.0, min(1.0, (low * 0.5 + mid * 0.7 + convective_proxy * 0.65) - 0.38))
        convection_factor = max(0.0, min(1.0, convective_proxy * 0.85 + precipitation_factor * 0.3))

        return low, mid, high, precipitation_factor, convection_factor

    def _classify_cloud_regime(
        self,
        low: float,
        mid: float,
        high: float,
        precip: float,
        convection: float,
        lat: float,
    ) -> str:
        if convection > 0.72 and precip > 0.55:
            return "deep_convection"
        if high > 0.62 and low < 0.35 and precip < 0.35:
            return "cirrus_sheet"
        if low > 0.66 and mid < 0.46 and high < 0.34 and abs(lat) <= 42:
            return "marine_stratocumulus"
        if (low + mid + high) / 3.0 > 0.52 and mid > 0.48:
            return "frontal_shield"
        return "cumulus_field"

    def _derive_band_architecture(
        self,
        regime: str,
        band: str,
        density: float,
        precip: float,
        convection: float,
        u: float,
        v: float,
    ) -> Dict[str, Any]:
        speed = math.hypot(float(u or 0.0), float(v or 0.0))
        regime_cfg = CLOUD_REGIMES[regime]

        if band == "low":
            base_alt = regime_cfg["base_altitude_m"]
            thickness = _lerp(700.0, 2200.0, density)
            if regime == "marine_stratocumulus":
                thickness *= 0.85
            lateral_scale = _lerp(70.0, regime_cfg["lateral_scale_km"], density)
        elif band == "mid":
            base_alt = max(2200.0, regime_cfg["base_altitude_m"] + 1900.0)
            thickness = _lerp(1000.0, 3200.0, density)
            lateral_scale = _lerp(60.0, regime_cfg["lateral_scale_km"] * 0.78, density)
        else:
            base_alt = max(6500.0, regime_cfg["base_altitude_m"] + 6200.0)
            thickness = _lerp(900.0, 2600.0, density)
            lateral_scale = _lerp(90.0, regime_cfg["lateral_scale_km"] * 1.05, density)

        if regime == "deep_convection":
            if band == "low":
                thickness *= _lerp(1.2, 1.7, convection)
            elif band == "mid":
                thickness *= _lerp(1.3, 1.9, convection)
            else:
                thickness *= _lerp(1.1, 1.6, convection)

        if regime == "cirrus_sheet" and band == "high":
            lateral_scale *= 1.35
            thickness *= 0.72

        top_alt = base_alt + thickness
        coverage = _clamp(density * 0.92 + precip * 0.18, 0.0, 1.0)

        return {
            "density": round(density, 4),
            "base_altitude_m": round(base_alt, 1),
            "top_altitude_m": round(top_alt, 1),
            "thickness_m": round(thickness, 1),
            "coverage": round(coverage, 4),
            "lateral_scale_km": round(lateral_scale, 1),
            "wind": {
                "u": round(float(u or 0.0), 3),
                "v": round(float(v or 0.0), 3),
                "speed_ms": round(speed, 3),
            },
        }

    def _derive_tile_cloud_architecture(
        self,
        tile_id: str,
        lat_center: float,
        lon_center: float,
        low: float,
        mid: float,
        high: float,
        precip: float,
        convection: float,
        wind_low: Dict[str, float],
        wind_mid: Dict[str, float],
        wind_high: Dict[str, float],
        seed: int,
    ) -> Dict[str, Any]:
        regime = self._classify_cloud_regime(low, mid, high, precip, convection, lat_center)
        cfg = CLOUD_REGIMES[regime]

        coverage = _clamp(low * 0.42 + mid * 0.33 + high * 0.25 + precip * 0.12, 0.0, 1.0)
        opacity = _clamp(0.18 + coverage * 0.64 + convection * 0.10, 0.0, 1.0)

        u_low = float((wind_low or {}).get("u", 0.0))
        v_low = float((wind_low or {}).get("v", 0.0))
        u_mid = float((wind_mid or {}).get("u", 0.0))
        v_mid = float((wind_mid or {}).get("v", 0.0))
        u_high = float((wind_high or {}).get("u", 0.0))
        v_high = float((wind_high or {}).get("v", 0.0))

        shear = math.hypot(u_high - u_low, v_high - v_low)
        wind_shear = _clamp(shear / 35.0, 0.0, 1.0)

        bands = {
            "low": self._derive_band_architecture(regime, "low", low, precip, convection, u_low, v_low),
            "mid": self._derive_band_architecture(regime, "mid", mid, precip, convection, u_mid, v_mid),
            "high": self._derive_band_architecture(regime, "high", high, precip, convection, u_high, v_high),
        }

        base_altitude_m = min(bands["low"]["base_altitude_m"], bands["mid"]["base_altitude_m"], bands["high"]["base_altitude_m"])
        top_altitude_m = max(bands["low"]["top_altitude_m"], bands["mid"]["top_altitude_m"], bands["high"]["top_altitude_m"])
        vertical_depth_m = max(0.0, top_altitude_m - base_altitude_m)

        mean_u = (u_low + u_mid + u_high) / 3.0
        mean_v = (v_low + v_mid + v_high) / 3.0
        anvil_dir_deg = (math.degrees(math.atan2(mean_u, mean_v)) + 360.0) % 360.0
        anvil_spread_km = 0.0
        if regime == "deep_convection":
            anvil_spread_km = _lerp(35.0, 140.0, _clamp(convection * 0.75 + wind_shear * 0.25, 0.0, 1.0))
        elif regime in ("frontal_shield", "cirrus_sheet"):
            anvil_spread_km = _lerp(20.0, 90.0, _clamp(high * 0.7 + wind_shear * 0.3, 0.0, 1.0))

        organization = _clamp(0.25 + coverage * 0.35 + precip * 0.15 + convection * 0.25, 0.0, 1.0)
        underside_darkness = _clamp(cfg["underside_darkness"] + precip * 0.16 + convection * 0.14, 0.0, 1.0)
        fringe_softness = _clamp(cfg["fringe_softness"] - convection * 0.10 + high * 0.06, 0.0, 1.0)

        return {
            "regime": regime,
            "coverage": round(coverage, 4),
            "opacity": round(opacity, 4),
            "base_altitude_m": round(base_altitude_m, 1),
            "top_altitude_m": round(top_altitude_m, 1),
            "vertical_depth_m": round(vertical_depth_m, 1),
            "underside_darkness": round(underside_darkness, 4),
            "fringe_softness": round(fringe_softness, 4),
            "organization": round(organization, 4),
            "wind_shear": round(wind_shear, 4),
            "tower_bias": round(_clamp(cfg["tower_bias"] + convection * 0.18, 0.0, 1.0), 4),
            "deck_bias": round(_clamp(cfg["deck_bias"] + low * 0.08 - convection * 0.10, 0.0, 1.0), 4),
            "wispy_bias": round(_clamp(cfg["wispy_bias"] + high * 0.10, 0.0, 1.0), 4),
            "anvil_dir_deg": round(anvil_dir_deg, 2),
            "anvil_spread_km": round(anvil_spread_km, 1),
            "bands": bands,
        }

    def _build_cloud_subcells(self, seed: int, regime: str, coverage: float, convection: float, precip: float) -> List[Dict[str, Any]]:
        rng = random.Random(seed)
        base_count = 4
        if coverage > 0.5:
            base_count += 2
        if convection > 0.55:
            base_count += 2
        if regime == "deep_convection":
            base_count += 2

        subcells = []
        for idx in range(base_count):
            role = "fringe"
            if regime == "deep_convection" and idx == 0:
                role = "tower"
            elif idx < 2:
                role = "core"
            elif regime in ("marine_stratocumulus", "frontal_shield") and idx >= base_count - 2:
                role = "deck"
            elif regime == "cirrus_sheet":
                role = "wispy"

            subcells.append(
                {
                    "id": f"sc-{idx}",
                    "dx": round(rng.uniform(-0.38, 0.38), 4),
                    "dy": round(rng.uniform(-0.38, 0.38), 4),
                    "weight": round(_clamp(rng.uniform(0.45, 1.0) * (0.7 + coverage * 0.3), 0.0, 1.0), 4),
                    "role": role,
                }
            )
        return subcells

    def _cloud_tile_payload(self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, hour_bucket: int) -> Dict[str, Any]:
        lat_center = (lat_min + lat_max) / 2.0
        lon_center = (lon_min + lon_max) / 2.0

        low, mid, high, precip, convection = self._cloud_density_triplet(lat_center, lon_center, hour_bucket)

        lat_r = math.radians(lat_center)
        lon_r = math.radians(lon_center)
        t = hour_bucket / 5.0

        wind_low_u = round(3.5 + 7.5 * math.sin(lat_r + t * 0.15), 3)
        wind_low_v = round(2.0 * math.cos(lon_r - t * 0.12), 3)

        wind_mid_u = round(6.0 + 9.5 * math.sin(lat_r * 0.8 - lon_r * 0.3 + t * 0.14), 3)
        wind_mid_v = round(2.6 * math.cos(lon_r * 0.85 + t * 0.09), 3)

        wind_high_u = round(11.0 + 17.0 * math.sin(lat_r * 0.55 + lon_r * 0.22 - t * 0.16), 3)
        wind_high_v = round(3.8 * math.cos(lon_r * 0.6 - t * 0.11), 3)

        importance = _clamp(
            low * 0.22
            + mid * 0.24
            + high * 0.18
            + precip * 0.18
            + convection * 0.18,
            0.0,
            1.0,
        )

        lat_idx = int((lat_min + 90) // 6)
        lon_idx = int((lon_min + 180) // 6)

        seed = int((abs(lat_center) * 1000 + abs(lon_center) * 100 + hour_bucket) % 10_000_000)
        wind = {
            "low": {"u": wind_low_u, "v": wind_low_v},
            "mid": {"u": wind_mid_u, "v": wind_mid_v},
            "high": {"u": wind_high_u, "v": wind_high_v},
        }
        arch = self._derive_tile_cloud_architecture(
            tile_id=f"gfs-{lat_idx:02d}-{lon_idx:02d}",
            lat_center=lat_center,
            lon_center=lon_center,
            low=low,
            mid=mid,
            high=high,
            precip=precip,
            convection=convection,
            wind_low=wind["low"],
            wind_mid=wind["mid"],
            wind_high=wind["high"],
            seed=seed,
        )

        tile_item = {
            "tile_id": f"gfs-{lat_idx:02d}-{lon_idx:02d}",
            "bounds": {
                "lat_min": round(lat_min, 4),
                "lat_max": round(lat_max, 4),
                "lon_min": round(lon_min, 4),
                "lon_max": round(lon_max, 4),
                "lat_center": round(lat_center, 4),
                "lon_center": round(lon_center, 4),
            },
            "low_density": round(low, 4),
            "mid_density": round(mid, 4),
            "high_density": round(high, 4),
            "precipitation_factor": round(precip, 4),
            "convection_factor": round(convection, 4),
            "altitude_low": 1200,
            "altitude_mid": 4200,
            "altitude_high": 9000,
            "wind": wind,
            "seed": seed,
            "importance": round(importance, 4),
            "updated_at": self._now_ms(),
            **arch,
            "subcells": self._build_cloud_subcells(seed, arch["regime"], arch["coverage"], convection, precip),
        }
        return enrich_cloud_tile_geometry(tile_item)

    def extract_precip_rate_mm_hr(self, datasets: dict[str, Any]) -> Any:
        """Extract precip rate (mm/hr) from available GFS variables."""
        if np is None:
            return None
        surf = datasets.get("surface")
        if surf is None:
            return None
        prate = self.safe_data_var(surf, ["prate", "PRATE", "tp", "unknown"])
        if prate is not None:
            arr = np.asarray(self.squeeze_forecast_array(prate).values, dtype=float) * 3600.0
            return np.clip(arr, 0.0, None)
        apcp = self.safe_data_var(surf, ["apcp", "APCP"])
        if apcp is not None:
            arr = np.asarray(self.squeeze_forecast_array(apcp).values, dtype=float)
            return np.clip(arr, 0.0, None)
        return None

    def classify_precip_rate_bucket(self, mm_hr: float) -> str:
        if mm_hr < PRECIP_BUCKETS_MM_HR[0]:
            return "white"
        if mm_hr < PRECIP_BUCKETS_MM_HR[1]:
            return "blue"
        if mm_hr < PRECIP_BUCKETS_MM_HR[2]:
            return "green"
        if mm_hr < PRECIP_BUCKETS_MM_HR[3]:
            return "yellow"
        if mm_hr < PRECIP_BUCKETS_MM_HR[4]:
            return "orange"
        if mm_hr < PRECIP_BUCKETS_MM_HR[5]:
            return "red"
        return "black"

    def compute_layer_rh(self, isobaric_ds: Any, top_hpa: int, bottom_hpa: int) -> Any:
        if np is None or isobaric_ds is None:
            return None
        rh = self.safe_data_var(isobaric_ds, ["r", "RH"])
        if rh is None or "isobaricInhPa" not in rh.dims:
            return None
        levels = np.asarray(isobaric_ds["isobaricInhPa"].values, dtype=float)
        sel = (levels <= bottom_hpa) & (levels >= top_hpa)
        if not np.any(sel):
            return None
        vals = np.asarray(self.squeeze_forecast_array(rh.sel(isobaricInhPa=levels[sel])).values, dtype=float)
        return np.nanmean(vals, axis=0)

    def estimate_cloud_base_top(self, isobaric_ds: Any, hgt_ds: Any = None) -> dict[str, Any]:
        if np is None or isobaric_ds is None:
            return {}
        rh = self.safe_data_var(isobaric_ds, ["r", "RH"])
        hgt = self.safe_data_var(isobaric_ds, ["gh", "HGT"])
        if rh is None or hgt is None:
            return {}
        rhv = np.asarray(self.squeeze_forecast_array(rh).values, dtype=float)
        hgtv = np.asarray(self.squeeze_forecast_array(hgt).values, dtype=float)
        sat = rhv >= 80.0
        if rhv.ndim < 3:
            return {}
        base_idx = np.argmax(sat, axis=0)
        top_idx = np.maximum(base_idx, rhv.shape[0] - 1 - np.argmax(np.flip(sat, axis=0), axis=0))
        base_m = np.take_along_axis(hgtv, np.expand_dims(base_idx, axis=0), axis=0)[0]
        top_m = np.take_along_axis(hgtv, np.expand_dims(top_idx, axis=0), axis=0)[0]
        return {"base_m": base_m, "top_m": top_m, "thickness_m": np.maximum(0.0, top_m - base_m)}

    def derive_cloud_layers(self, surface_ds: Any, agl_ds: Any, isobaric_ds: Any) -> dict[str, Any]:
        if np is None:
            return {}
        tcdc = None
        if surface_ds is not None:
            da = self.safe_data_var(surface_ds, ["tcc", "TCDC"])
            if da is not None:
                tcdc = np.asarray(self.squeeze_forecast_array(da).values, dtype=float)
        low = self.compute_layer_rh(isobaric_ds, 850, 1000)
        mid = self.compute_layer_rh(isobaric_ds, 600, 850)
        high = self.compute_layer_rh(isobaric_ds, 300, 600)
        low_occ = np.clip(((low if low is not None else 40.0) / 100.0), 0.0, 1.0)
        mid_occ = np.clip(((mid if mid is not None else 35.0) / 100.0), 0.0, 1.0)
        high_occ = np.clip(((high if high is not None else 30.0) / 100.0), 0.0, 1.0)
        if tcdc is not None:
            tcdc_n = np.clip(tcdc / 100.0, 0.0, 1.0)
            low_occ = np.clip(low_occ * 0.7 + tcdc_n * 0.3, 0.0, 1.0)
            mid_occ = np.clip(mid_occ * 0.75 + tcdc_n * 0.25, 0.0, 1.0)
            high_occ = np.clip(high_occ * 0.78 + tcdc_n * 0.22, 0.0, 1.0)
        alt = self.estimate_cloud_base_top(isobaric_ds)
        return {"low": low_occ, "mid": mid_occ, "high": high_occ, "alt": alt}

    def wind_speed_dir_from_uv(self, u: float, v: float) -> tuple[float, float]:
        speed_mps = math.hypot(u, v)
        heading_deg = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
        return speed_mps, heading_deg

    def select_balloon_steering_level(self, datasets: dict[str, Any], target_ft: int = 10000) -> dict[str, Any]:
        target_hpa = 700
        return {"type": "isobaricInhPa", "level_hpa": target_hpa, "note": "nearest representative level"}

    def derive_balloon_vectors(self, datasets: dict[str, Any], target_ft: int = 10000) -> list[dict[str, Any]]:
        if np is None:
            return []
        iso = datasets.get("isobaricInhPa")
        if iso is None:
            return []
        u_da = self.safe_data_var(iso, ["u", "UGRD"])
        v_da = self.safe_data_var(iso, ["v", "VGRD"])
        if u_da is None or v_da is None or "isobaricInhPa" not in u_da.dims:
            return []
        levels = np.asarray(iso["isobaricInhPa"].values, dtype=float)
        level = 700.0 if 700.0 in levels else float(levels[np.argmin(np.abs(levels - 700.0))])
        u = np.asarray(self.squeeze_forecast_array(u_da.sel(isobaricInhPa=level)).values, dtype=float)
        v = np.asarray(self.squeeze_forecast_array(v_da.sel(isobaricInhPa=level)).values, dtype=float)
        lat2d, lon2d = self.ensure_lat_lon_2d(iso)
        if lat2d is None or lon2d is None:
            return []
        vectors: list[dict[str, Any]] = []
        step_y = max(1, u.shape[0] // 18)
        step_x = max(1, u.shape[1] // 36)
        for iy in range(0, u.shape[0], step_y):
            for ix in range(0, u.shape[1], step_x):
                speed_mps, heading_deg = self.wind_speed_dir_from_uv(float(u[iy, ix]), float(v[iy, ix]))
                vectors.append({"lat": float(lat2d[iy, ix]), "lon": float(lon2d[iy, ix]), "u": float(u[iy, ix]), "v": float(v[iy, ix]), "speed_mps": round(speed_mps, 3), "heading_deg": round(heading_deg, 2), "source_level": f"{int(level)} hPa"})
        return vectors

    def derive_hail_mask(self, datasets: dict[str, Any], precip_mm_hr: Any, cloud_layers: dict[str, Any]) -> Any:
        if np is None or precip_mm_hr is None:
            return None
        surf = datasets.get("surface")
        cape = None
        if surf is not None:
            da = self.safe_data_var(surf, ["cape", "CAPE"])
            if da is not None:
                cape = np.asarray(self.squeeze_forecast_array(da).values, dtype=float)
        deep = cloud_layers.get("high") if isinstance(cloud_layers, dict) else None
        if cape is None:
            cape = np.zeros_like(precip_mm_hr)
        if deep is None:
            deep = np.zeros_like(precip_mm_hr)
        return (cape > 900.0) & (precip_mm_hr > 3.0) & (deep > 0.55)

    def derive_lightning_mask(self, datasets: dict[str, Any], precip_mm_hr: Any, cloud_layers: dict[str, Any]) -> Any:
        if np is None or precip_mm_hr is None:
            return None
        surf = datasets.get("surface")
        cape = np.zeros_like(precip_mm_hr)
        cin = np.zeros_like(precip_mm_hr)
        if surf is not None:
            d_cape = self.safe_data_var(surf, ["cape", "CAPE"])
            d_cin = self.safe_data_var(surf, ["cin", "CIN"])
            if d_cape is not None:
                cape = np.asarray(self.squeeze_forecast_array(d_cape).values, dtype=float)
            if d_cin is not None:
                cin = np.asarray(self.squeeze_forecast_array(d_cin).values, dtype=float)
        high = cloud_layers.get("high") if isinstance(cloud_layers, dict) else np.zeros_like(precip_mm_hr)
        return (cape > 650.0) & (cin > -160.0) & (precip_mm_hr > 1.4) & (high > 0.5)

    def threshold_to_mask(self, array: Any, threshold: float) -> Any:
        if np is None or array is None:
            return None
        return np.asarray(array) >= threshold

    def connected_components_or_simple_cell_polygons(self, mask: Any, lat2d: Any, lon2d: Any) -> list[dict[str, Any]]:
        if np is None or mask is None or lat2d is None or lon2d is None:
            return []
        polys: list[dict[str, Any]] = []
        ys, xs = np.where(mask)
        for y, x in zip(ys.tolist(), xs.tolist()):
            dlat = 0.12
            dlon = 0.12
            ring = close_ring([
                {"lat": float(lat2d[y, x] - dlat), "lng": float(lon2d[y, x] - dlon)},
                {"lat": float(lat2d[y, x] - dlat), "lng": float(lon2d[y, x] + dlon)},
                {"lat": float(lat2d[y, x] + dlat), "lng": float(lon2d[y, x] + dlon)},
                {"lat": float(lat2d[y, x] + dlat), "lng": float(lon2d[y, x] - dlon)},
            ])
            polys.append({"points": ring})
            if len(polys) >= 3500:
                break
        return polys


    def derive_precip_columns_from_tiles(self, tiles: list[dict[str, Any]], max_items: int = 260) -> list[dict[str, Any]]:
        """Build visualization-oriented precipitation columns from tile proxies.

        These are estimated scene hints (not direct observed column geometry).
        """
        out: list[dict[str, Any]] = []
        ranked = sorted(tiles or [], key=lambda t: float((t or {}).get("precip_rate") or ((t or {}).get("precipitation_factor", 0) * 45.0)), reverse=True)
        for t in ranked:
            precip_rate = float(t.get("precip_rate") or (float(t.get("precipitation_factor", 0.0)) * 45.0))
            if precip_rate < 1.2:
                continue
            bounds = t.get("bounds") or {}
            base_alt = float(t.get("base_altitude_m") or ((t.get("bands") or {}).get("low") or {}).get("base_altitude_m") or 1800.0)
            top_alt = float(t.get("top_altitude_m") or ((t.get("bands") or {}).get("mid") or {}).get("top_altitude_m") or 5200.0)
            virga_stop = 0.0
            if precip_rate < 3.0 and base_alt > 2200.0:
                virga_stop = min(base_alt - 120.0, max(250.0, base_alt * 0.18))
            out.append({
                "tile_id": t.get("tile_id"),
                "lat": float(bounds.get("lat_center") or 0.0),
                "lon": float(bounds.get("lon_center") or 0.0),
                "estimated_source_altitude_m": round(max(300.0, base_alt), 1),
                "estimated_top_altitude_m": round(max(base_alt, top_alt), 1),
                "estimated_surface_altitude_m": round(max(0.0, virga_stop), 1),
                "estimated_precip_rate_mm_hr": round(max(0.0, precip_rate), 3),
                "estimated_intensity": round(max(0.0, min(1.0, precip_rate / 45.0)), 4),
                "type_hint": "convective" if float(t.get("storm_energy") or t.get("convection_factor") or 0.0) > 0.62 else "rain",
                "wind_u": float(t.get("wind_u") or ((t.get("wind") or {}).get("mid") or {}).get("u") or 0.0),
                "wind_v": float(t.get("wind_v") or ((t.get("wind") or {}).get("mid") or {}).get("v") or 0.0),
                "estimated": True,
            })
            if len(out) >= max_items:
                break
        return out

    def derive_lightning_events_from_tiles(self, tiles: list[dict[str, Any]], max_items: int = 140) -> list[dict[str, Any]]:
        """Build estimated lightning events from storm-strength proxies."""
        out: list[dict[str, Any]] = []
        ranked = sorted(tiles or [], key=lambda t: float(t.get("storm_energy") or t.get("convection_factor") or 0.0), reverse=True)
        for t in ranked:
            energy = float(t.get("storm_energy") or t.get("convection_factor") or 0.0)
            precip = float(t.get("precip_rate") or (float(t.get("precipitation_factor") or 0.0) * 45.0))
            if energy < 0.48 or precip < 4.0:
                continue
            bounds = t.get("bounds") or {}
            top_alt = float(t.get("top_altitude_m") or ((t.get("bands") or {}).get("high") or {}).get("top_altitude_m") or 9000.0)
            out.append({
                "tile_id": t.get("tile_id"),
                "lat": float(bounds.get("lat_center") or 0.0),
                "lon": float(bounds.get("lon_center") or 0.0),
                "estimated_flash_top_m": round(max(900.0, top_alt), 1),
                "estimated_flash_bottom_m": round(max(80.0, top_alt * (0.24 if energy > 0.72 else 0.42)), 1),
                "estimated_energy": round(max(0.0, min(1.0, energy)), 4),
                "severity_hint": "severe" if energy > 0.72 else "active",
                "estimated": True,
            })
            if len(out) >= max_items:
                break
        return out
    def serialize_cloud_payload(self, tiles: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "source": meta.get("source", "gfs_nomads"), "heuristic": meta.get("source") != "gfs_nomads", "updated_at": self._now_ms(), "items": tiles, "summary": {"tile_count": len(tiles)}, "cycle": meta.get("cycle"), "forecast_hour": meta.get("forecast_hour"), "valid_time": meta.get("valid_time"), "bbox_used": meta.get("bbox_used")}

    def serialize_rain_payload(self, rain_polygons: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": rain_polygons, "count": len(rain_polygons)}

    def serialize_hail_payload(self, hail_polygons: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": hail_polygons, "count": len(hail_polygons)}

    def serialize_lightning_payload(self, lightning_polygons: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": lightning_polygons, "count": len(lightning_polygons)}

    def serialize_balloon_payload(self, vectors: list[dict[str, Any]]) -> dict[str, Any]:
        return {"items": vectors, "count": len(vectors)}

    def bbox_cache_key(self, bbox: dict[str, float]) -> str:
        return f"{bbox.get('west')}:{bbox.get('south')}:{bbox.get('east')}:{bbox.get('north')}"

    def payload_cache_key(self, cycle: str, forecast_hour: int, bbox: dict[str, float]) -> str:
        return f"gfs_payload:v2:{cycle}:{forecast_hour}:{self.bbox_cache_key(bbox)}"

    def read_cached_payload(self, key: str) -> Any:
        if self.disk_cache is None:
            return None
        return self.disk_cache.get(key)

    def write_cached_payload(self, key: str, payload: Any) -> None:
        if self.disk_cache is None:
            return
        self.disk_cache.set(key, payload, expire=DEFAULT_GFS_CACHE_TTL_SECONDS)

    def _tile_from_real_fields(self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, low: float, mid: float, high: float, precip: float, conv: float, u: float, v: float, seed_hint: str) -> dict[str, Any]:
        hour_bucket = int(time.time() // 3600)
        tile = self._cloud_tile_payload(lat_min, lat_max, lon_min, lon_max, hour_bucket)
        tile["low_density"] = round(float(low), 4)
        tile["mid_density"] = round(float(mid), 4)
        tile["high_density"] = round(float(high), 4)
        tile["precipitation_factor"] = round(float(precip), 4)
        tile["convection_factor"] = round(float(conv), 4)
        tile["seed"] = stable_hash_u32(seed_hint)
        for b in ("low", "mid", "high"):
            tile["bands"][b]["wind"]["u"] = round(float(u), 3)
            tile["bands"][b]["wind"]["v"] = round(float(v), 3)
        return enrich_cloud_tile_geometry(tile)

    def _derive_real_source_fields(self, groups: dict[str, Any]) -> dict[str, Any]:
        precip = self.extract_precip_rate_mm_hr(groups)
        hagl = groups.get("10m")
        if hagl is None:
            hagl = groups.get("2m")
        cloud_layers = self.derive_cloud_layers(groups.get("surface"), hagl, groups.get("isobaricInhPa"))
        vectors = self.derive_balloon_vectors(groups)
        if precip is None or not cloud_layers:
            raise RuntimeError("missing precip/cloud arrays")

        sample_ds = groups.get("surface")
        if sample_ds is None:
            sample_ds = groups.get("isobaricInhPa")
        if sample_ds is None:
            sample_ds = groups.get("10m")
        if sample_ds is None:
            sample_ds = groups.get("2m")
        lat2d, lon2d = self.ensure_lat_lon_2d(sample_ds)
        if lat2d is None or lon2d is None:
            raise RuntimeError("missing lat lon grid")

        high = cloud_layers.get("high")
        low = cloud_layers.get("low")
        mid = cloud_layers.get("mid")
        conv = np.clip((precip / 30.0) * 0.6 + high * 0.4, 0.0, 1.0)
        humidity = self._extract_scalar_field(groups, [("isobaricInhPa", ["r", "RH"]), ("surface", ["r", "RH"])])
        wind_u = self._extract_scalar_field(groups, [("10m", ["u", "UGRD"]), ("isobaricInhPa", ["u", "UGRD"]), ("surface", ["u", "UGRD"])])
        wind_v = self._extract_scalar_field(groups, [("10m", ["v", "VGRD"]), ("isobaricInhPa", ["v", "VGRD"]), ("surface", ["v", "VGRD"])])
        wind_speed = np.sqrt(np.square(wind_u) + np.square(wind_v)) if wind_u is not None and wind_v is not None else (np.sqrt(np.square(vectors[0]["u"]) + np.square(vectors[0]["v"])) if vectors else np.zeros_like(precip))
        temp_k = self._extract_scalar_field(groups, [("surface", ["t", "TMP", "tmp"]), ("2m", ["t", "TMP", "tmp"])])
        pressure_pa = self._extract_scalar_field(groups, [("meanSea", ["prmsl", "PRMSL"]), ("surface", ["prmsl", "PRMSL"])])
        return {
            "precip": precip,
            "cloud_layers": cloud_layers,
            "vectors": vectors,
            "lat2d": lat2d,
            "lon2d": lon2d,
            "high": high,
            "low": low,
            "mid": mid,
            "conv": conv,
            "cloud_density": np.clip((low + mid + high) / 3.0, 0.0, 1.0),
            "precip_rate": precip,
            "wind_speed": wind_speed,
            "temperature_k": temp_k,
            "pressure_pa": pressure_pa,
            "humidity": humidity,
            "wind_u": wind_u,
            "wind_v": wind_v,
        }


    def _extract_scalar_field(self, groups: dict[str, Any], candidates: list[tuple[str, list[str]]]) -> Any:
        for group_name, names in candidates:
            ds = groups.get(group_name)
            arr = self.safe_data_var(ds, names)
            arr = self.squeeze_forecast_array(arr)
            if arr is None:
                continue
            if np is None:
                continue
            try:
                vals = np.asarray(arr.values, dtype=float)
                if vals.ndim == 2 and vals.size:
                    return vals
            except Exception:
                continue
        return None

    def _store_scalar_fields(self, fields: dict[str, Any]) -> None:
        if np is None:
            return
        lat2d = fields.get("lat2d")
        lon2d = fields.get("lon2d")
        if lat2d is None or lon2d is None:
            return
        try:
            lat_arr = np.asarray(lat2d, dtype=float)
            lon_arr = np.asarray(lon2d, dtype=float)
        except Exception:
            return
        scalar_fields: dict[str, dict[str, Any]] = {}
        for key in ("cloud_density", "precip_rate", "wind_speed", "temperature_k", "pressure_pa", "humidity", "wind_u", "wind_v"):
            val = fields.get(key)
            if val is None:
                continue
            try:
                arr = np.asarray(val, dtype=float)
            except Exception:
                continue
            if arr.ndim != 2:
                continue
            scalar_fields[key] = {
                "lat": lat_arr.tolist(),
                "lon": lon_arr.tolist(),
                "values": arr.tolist(),
                "updated_at": self._now_ms(),
            }
        self.state.scalar_fields = scalar_fields

    def tile_to_bounds(self, z: int, x: int, y: int) -> dict[str, float]:
        return self._tile_bounds_xyz(z, x, y)

    def bounds_to_tile_range(self, bounds: dict[str, float], z: int) -> dict[str, int]:
        z = max(0, int(z))
        n = 2 ** z
        b = self._normalize_bbox(bounds)
        nw = self._lat_lon_to_tile(b["north"], b["west"], z)
        se = self._lat_lon_to_tile(b["south"], b["east"], z)
        return {
            "x_min": max(0, min(n - 1, int(min(nw["x"], se["x"])))),
            "x_max": max(0, min(n - 1, int(max(nw["x"], se["x"])))),
            "y_min": max(0, min(n - 1, int(min(nw["y"], se["y"])))),
            "y_max": max(0, min(n - 1, int(max(nw["y"], se["y"])))),
        }

    def _lat_lon_to_tile(self, lat: float, lon: float, z: int) -> dict[str, int]:
        lat = max(-85.05112878, min(85.05112878, safe_float(lat, 0.0)))
        lon = max(-180.0, min(180.0, safe_float(lon, 0.0)))
        n = 2 ** max(0, int(z))
        x = int((lon + 180.0) / 360.0 * n)
        lat_rad = math.radians(lat)
        y = int((1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * n)
        return {"x": max(0, min(n - 1, x)), "y": max(0, min(n - 1, y))}

    def _marching_squares_contours(self, field_name: str, bounds: dict[str, float], z: int) -> list[dict[str, Any]]:
        if np is None:
            return []
        sf = (self.state.scalar_fields or {}).get(field_name)
        if not sf:
            return []
        try:
            lat = np.asarray(sf.get("lat"), dtype=float)
            lon = np.asarray(sf.get("lon"), dtype=float)
            vals = np.asarray(sf.get("values"), dtype=float)
        except Exception:
            return []
        if vals.ndim != 2 or vals.shape[0] < 2 or vals.shape[1] < 2:
            return []
        detail_stride = 4 if z < 3 else 2 if z < 6 else 1
        levels = {
            "precip_rate": [0.1, 1.0, 4.0, 12.0],
            "cloud_density": [0.2, 0.4, 0.65, 0.85],
            "pressure_pa": [99500.0, 100500.0, 101300.0],
            "temperature_k": [273.15, 283.15, 293.15, 303.15],
        }.get(field_name, [0.3, 0.6])
        south, north = bounds.get("south", -90.0), bounds.get("north", 90.0)
        west, east = bounds.get("west", -180.0), bounds.get("east", 180.0)
        out = []
        max_features = 60 if z < 3 else 140 if z < 6 else 260
        for lvl in levels:
            for y in range(0, vals.shape[0] - 1, detail_stride):
                for x in range(0, vals.shape[1] - 1, detail_stride):
                    v00 = safe_float(vals[y, x], 0.0)
                    v10 = safe_float(vals[y, x + 1], 0.0)
                    v01 = safe_float(vals[y + 1, x], 0.0)
                    v11 = safe_float(vals[y + 1, x + 1], 0.0)
                    mask = (1 if v00 >= lvl else 0) | (2 if v10 >= lvl else 0) | (4 if v11 >= lvl else 0) | (8 if v01 >= lvl else 0)
                    if mask in (0, 15):
                        continue
                    lat0 = safe_float(lat[y, x], None); lat1 = safe_float(lat[y + 1, x + 1], None)
                    lon0 = safe_float(lon[y, x], None); lon1 = safe_float(lon[y + 1, x + 1], None)
                    if None in (lat0, lat1, lon0, lon1):
                        continue
                    c_lat = (lat0 + lat1) * 0.5
                    c_lon = (lon0 + lon1) * 0.5
                    if not (south <= c_lat <= north and west <= c_lon <= east):
                        continue
                    poly = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
                    out.append({
                        "type": "Feature",
                        "properties": {"field": field_name, "level": lvl, "mask": mask, "lod_z": z},
                        "geometry": {"type": "Polygon", "coordinates": [poly]},
                    })
                    if len(out) >= max_features:
                        return out
        return out

    def _derive_real_cloud_tiles(self, fields: dict[str, Any], cycle: str, forecast_hour: int) -> list[dict[str, Any]]:
        precip = fields["precip"]
        lat2d = fields["lat2d"]
        lon2d = fields["lon2d"]
        low = fields["low"]
        mid = fields["mid"]
        high = fields["high"]
        conv = fields["conv"]
        vectors = fields["vectors"]

        tiles: list[dict[str, Any]] = []
        y_step = max(1, precip.shape[0] // 28)
        x_step = max(1, precip.shape[1] // 56)
        for y in range(0, precip.shape[0] - y_step, y_step):
            for x in range(0, precip.shape[1] - x_step, x_step):
                lat_min = float(np.min(lat2d[y:y+y_step, x:x+x_step]))
                lat_max = float(np.max(lat2d[y:y+y_step, x:x+x_step]))
                lon_min = float(np.min(lon2d[y:y+y_step, x:x+x_step]))
                lon_max = float(np.max(lon2d[y:y+y_step, x:x+x_step]))
                low_v = float(np.nanmean(low[y:y+y_step, x:x+x_step]))
                mid_v = float(np.nanmean(mid[y:y+y_step, x:x+x_step]))
                high_v = float(np.nanmean(high[y:y+y_step, x:x+x_step]))
                precip_v = float(np.nanmean(np.clip(precip[y:y+y_step, x:x+x_step] / 45.0, 0.0, 1.0)))
                conv_v = float(np.nanmean(conv[y:y+y_step, x:x+x_step]))
                if max(low_v, mid_v, high_v) < 0.06:
                    continue
                u = vectors[0]["u"] if vectors else 0.0
                v = vectors[0]["v"] if vectors else 0.0
                tile = self._tile_from_real_fields(
                    lat_min,
                    lat_max,
                    lon_min,
                    lon_max,
                    low_v,
                    mid_v,
                    high_v,
                    precip_v,
                    conv_v,
                    u,
                    v,
                    f"{cycle}:{forecast_hour}:{y}:{x}",
                )
                tiles.append(tile)
        return tiles

    def _derive_real_hazard_payloads(self, groups: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
        precip = fields["precip"]
        cloud_layers = fields["cloud_layers"]
        lat2d = fields["lat2d"]
        lon2d = fields["lon2d"]
        rain_mask = self.threshold_to_mask(precip, 0.5)
        hail_mask = self.derive_hail_mask(groups, precip, cloud_layers)
        lightning_mask = self.derive_lightning_mask(groups, precip, cloud_layers)
        rain_polys = self.connected_components_or_simple_cell_polygons(rain_mask, lat2d, lon2d)
        hail_polys = self.connected_components_or_simple_cell_polygons(hail_mask, lat2d, lon2d)
        lightning_polys = self.connected_components_or_simple_cell_polygons(lightning_mask, lat2d, lon2d)
        return {
            "rain": self.serialize_rain_payload(rain_polys),
            "hail": self.serialize_hail_payload(hail_polys),
            "lightning": self.serialize_lightning_payload(lightning_polys),
        }

    def generate_real_gfs_payload(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        bbox = self._normalize_bbox(bbox)
        ingest = self.ingest_latest_model_fields(bbox)
        fetch = ingest["fetch"]
        groups = ingest["groups"]
        mode = ingest.get("mode", "live")

        try:
            fields = self._derive_real_source_fields(groups)
            self._store_scalar_fields(fields)
            tiles = self._derive_real_cloud_tiles(fields, fetch.cycle, fetch.forecast_hour)
            hazards = self._derive_real_hazard_payloads(groups, fields)
            payload = {
                "source": "gfs_nomads",
                "cycle": fetch.cycle,
                "forecast_hour": fetch.forecast_hour,
                "valid_time": fetch.valid_time,
                "bbox_used": bbox,
                "tiles": tiles,
                "rain": hazards["rain"],
                "hail": hazards["hail"],
                "lightning": hazards["lightning"],
                "balloons": self.serialize_balloon_payload(fields["vectors"]),
                "heuristic": False,
                "quality_note": "Cloud/precip overlays are derived from real NOMADS GFS GRIB2 fields; geometry remains visualization-oriented.",
                "source_format": "grib2",
                "source_url": fetch.url,
                "cache_path": str(fetch.path) if fetch.path else None,
                "fields_available": list(self.state.fields_available or []),
                "fields_missing": list(self.state.fields_missing or []),
                "using_last_known_good": mode == "last_known_good",
                "degraded_mode": mode != "live",
                "decode_backend": self.state.decode_backend,
                "data_source_mode": self.state.data_source_mode,
            }
            return self._annotate_weather_payload(
                payload,
                bbox=bbox,
                source="gfs_nomads",
                payload_state="live" if mode == "live" else "cached",
                heuristic=False,
                quality_note=payload["quality_note"] if mode == "live" else "Serving last-known-good GRIB2 decode after live fetch/decode failure.",
                confidence="high" if mode == "live" else "medium",
            )
        finally:
            self._release_groups(groups)
            groups = None
            gc.collect()

    def generate_fallback_payload(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        bbox = self._normalize_bbox(bbox)
        cloud = self._legacy_cloud_tiles_payload()
        cloud.update({
            "source": "fallback_proxy",
            "payload_state": "synthetic",
            "cycle": cloud.get("cycle"),
            "forecast_hour": cloud.get("forecast_hour"),
            "valid_time": cloud.get("valid_time"),
            "bbox_used": bbox,
            "quality_note": "Synthetic fallback generated from heuristic cloud-state proxies; not direct observational truth.",
            "confidence": "low",
        })
        cloud["rain"] = {"items": [], "count": 0}
        cloud["hail"] = {"items": [], "count": 0}
        cloud["lightning"] = {"items": [], "count": 0}
        cloud["balloons"] = {"items": [], "count": 0}
        cloud["decode_backend"] = "none"
        cloud["data_source_mode"] = "heuristic"
        return self._annotate_weather_payload(
            cloud,
            bbox=bbox,
            source="fallback_proxy",
            payload_state="synthetic",
            heuristic=True,
            quality_note=cloud["quality_note"],
            confidence="low",
        )


    def read_most_recent_cached_real_payload(self, max_age_seconds: int = 5400) -> Any:
        if self.disk_cache is None:
            return None
        now_ts = time.time()
        newest = None
        newest_ts = 0.0
        try:
            for k in self.disk_cache.iterkeys():
                if not str(k).startswith('gfs_payload:v2:'):
                    continue
                row = self.disk_cache.get(k)
                if not isinstance(row, dict):
                    continue
                if row.get('source') != 'gfs_nomads':
                    continue
                ts = float(row.get('updated_at', 0)) / 1000.0 if row.get('updated_at') else 0.0
                if ts <= 0:
                    continue
                if now_ts - ts > max_age_seconds:
                    continue
                if ts > newest_ts:
                    newest_ts = ts
                    newest = row
        except Exception:
            return None
        return newest

    def generate_weather_payload(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        bbox = self._normalize_bbox(bbox)
        try:
            payload = self.generate_real_gfs_payload(bbox)
            key = self.payload_cache_key(payload.get("cycle", "na"), int(payload.get("forecast_hour", 0)), bbox or {})
            self.write_cached_payload(key, payload)
            return payload
        except Exception as exc:
            print(f"[gfs] real nomads path failed in generate_weather_payload/generate_real_gfs_payload; trying cached real payload first: {type(exc).__name__}: {exc}")
            cached = self.read_most_recent_cached_real_payload(max_age_seconds=5400)
            if isinstance(cached, dict):
                cached_payload = self._annotate_weather_payload(
                    dict(cached),
                    bbox=bbox,
                    source="gfs_nomads",
                    payload_state="cached",
                    heuristic=False,
                    quality_note="Serving recent cached NOMADS-derived payload because live fetch failed.",
                    confidence="medium",
                )
                return cached_payload
            fb = self.generate_fallback_payload(bbox)
            return fb

    def debug_real_gfs_cycle(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        """Manual debug helper for cycle/hour/url/group visibility."""
        bbox = bbox or {"west": -130, "south": 20, "east": -60, "north": 55}
        now = utc_now()
        fetch = self.gfs_client.fetch_latest_available_subset(now, bbox, DEFAULT_REQUIRED_VARIABLES, DEFAULT_REQUIRED_LEVELS)
        groups, decode_backend = self.open_all_valid_groups(fetch.path) if fetch.ok and fetch.path else ({}, "none")
        return {
            "ok": fetch.ok,
            "cycle": fetch.cycle,
            "forecast_hour": fetch.forecast_hour,
            "url": fetch.url,
            "groups": {k: list(v.data_vars.keys())[:20] for k, v in groups.items()},
            "decode_backend": decode_backend,
            "error": fetch.error,
        }

    def validate_bbox_real_fields(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        """Manual check that precip/cloud/wind products are non-empty."""
        payload = self.generate_weather_payload(bbox)
        return {
            "source": payload.get("source"),
            "has_precip": bool((payload.get("rain") or {}).get("count", 0) > 0),
            "has_cloud": bool(len(payload.get("tiles") or payload.get("items") or []) > 0),
            "has_wind": bool((payload.get("balloons") or {}).get("count", 0) > 0),
        }

    def compare_fallback_vs_real(self, bbox: dict[str, float] | None = None) -> dict[str, Any]:
        """Manual comparison helper between fallback and real precipitation/cloud outputs."""
        real = self.generate_weather_payload(bbox)
        fb = self.generate_fallback_payload(bbox)
        return {
            "real_source": real.get("source"),
            "real_tiles": len(real.get("tiles") or real.get("items") or []),
            "real_rain_cells": (real.get("rain") or {}).get("count", 0),
            "fallback_tiles": len(fb.get("items") or []),
            "fallback_rain_cells": (fb.get("rain") or {}).get("count", 0),
        }

    def _legacy_cloud_tiles_payload(self) -> Dict[str, Any]:
        now_ms = self._now_ms()
        hour_bucket = now_ms // 3_600_000

        tiles: List[Dict[str, Any]] = []
        for lat in range(-90, 90, 6):
            lat_min = float(lat)
            lat_max = float(lat + 6)
            for lon in range(-180, 180, 6):
                lon_min = float(lon)
                lon_max = float(lon + 6)
                tile = self._cloud_tile_payload(lat_min, lat_max, lon_min, lon_max, hour_bucket)
                if max(tile["low_density"], tile["mid_density"], tile["high_density"]) < 0.08:
                    continue
                tiles.append(tile)

        tiles.sort(key=lambda t: t["importance"], reverse=True)
        summary = {
            "tile_count": len(tiles),
            "strong_tile_count": len([t for t in tiles if t["importance"] >= 0.72]),
            "storm_tile_count": len([t for t in tiles if t["precipitation_factor"] >= 0.55 or t["convection_factor"] >= 0.6]),
        }

        return {
            "ok": True,
            "source": "heuristic_forecast_from_latest_state",
            "heuristic": True,
            "note": "Fallback heuristic payload because real GFS path unavailable.",
            "updated_at": now_ms,
            "items": tiles,
            "summary": summary,
        }


    def _cloud_feature_from_tile(self, tile: dict[str, Any]) -> dict[str, Any]:
        bounds = tile.get("bounds") or {}
        low = (tile.get("bands") or {}).get("low") or {}
        mid = (tile.get("bands") or {}).get("mid") or {}
        high = (tile.get("bands") or {}).get("high") or {}
        return {
            "id": str(tile.get("tile_id") or f"cloud-{stable_hash_u32(str(bounds))}"),
            "center": {"lat": float(bounds.get("lat_center") or 0.0), "lon": float(bounds.get("lon_center") or 0.0)},
            "footprint": (low.get("footprints") or [{"points": []}])[0].get("points", []),
            "estimated_cloud_base_m": float(tile.get("estimated_cloud_base_m") or tile.get("base_altitude_m") or low.get("base_altitude_m") or 1000.0),
            "estimated_cloud_top_m": float(tile.get("estimated_cloud_top_m") or tile.get("top_altitude_m") or high.get("top_altitude_m") or 8000.0),
            "estimated_thickness_m": float(tile.get("estimated_cloud_thickness_m") or tile.get("vertical_depth_m") or 2200.0),
            "estimated_density": float(tile.get("estimated_density") or tile.get("density") or 0.0),
            "opacity_hint": round(_clamp(0.14 + float(tile.get("density") or 0.0) * 0.54, 0.08, 0.8), 4),
            "convective_strength": float(tile.get("storm_energy") or tile.get("convection_factor") or 0.0),
            "cloud_type_hint": str(tile.get("regime") or "cumulus_field"),
            "layer_hints": [
                {
                    "band": "low",
                    "base_m": float(low.get("base_altitude_m") or 0.0),
                    "top_m": float(low.get("top_altitude_m") or 0.0),
                    "density": float(low.get("density") or tile.get("low_density") or 0.0),
                    "spread_km": float(low.get("lateral_scale_km") or 0.0),
                },
                {
                    "band": "mid",
                    "base_m": float(mid.get("base_altitude_m") or 0.0),
                    "top_m": float(mid.get("top_altitude_m") or 0.0),
                    "density": float(mid.get("density") or tile.get("mid_density") or 0.0),
                    "spread_km": float(mid.get("lateral_scale_km") or 0.0),
                },
                {
                    "band": "high",
                    "base_m": float(high.get("base_altitude_m") or 0.0),
                    "top_m": float(high.get("top_altitude_m") or 0.0),
                    "density": float(high.get("density") or tile.get("high_density") or 0.0),
                    "spread_km": float(high.get("lateral_scale_km") or 0.0),
                },
            ],
            "visual_priority": float(tile.get("importance") or 0.0),
            "noise_seed": int(tile.get("seed") or stable_hash_u32(str(tile.get("tile_id") or "cloud"))),
            "heuristic": True,
            "source_confidence": "estimated",
            "source_fields": {
                "proxy_precip_rate": float(tile.get("precip_rate") or 0.0),
                "proxy_convection": float(tile.get("convection_factor") or tile.get("storm_energy") or 0.0),
                "proxy_low_density": float(tile.get("low_density") or 0.0),
                "proxy_mid_density": float(tile.get("mid_density") or 0.0),
                "proxy_high_density": float(tile.get("high_density") or 0.0),
            },
        }

    def _precip_feature_from_column(self, col: dict[str, Any]) -> dict[str, Any]:
        rate = float(col.get("estimated_precip_rate_mm_hr") or 0.0)
        intensity = _clamp(rate / 45.0, 0.0, 1.0)
        if intensity >= 0.9:
            bucket = "black"
            cls = "extreme"
        elif intensity >= 0.75:
            bucket = "red"
            cls = "very_heavy"
        elif intensity >= 0.6:
            bucket = "orange"
            cls = "heavy"
        elif intensity >= 0.45:
            bucket = "yellow"
            cls = "moderate"
        elif intensity >= 0.3:
            bucket = "green"
            cls = "light_moderate"
        elif intensity >= 0.16:
            bucket = "blue"
            cls = "light"
        else:
            bucket = "white"
            cls = "very_light"
        lat = float(col.get("lat") or 0.0)
        lon = float(col.get("lon") or 0.0)
        spread = 0.08 + intensity * 0.18
        footprint = close_ring([
            {"lat": lat - spread, "lng": lon - spread},
            {"lat": lat - spread, "lng": lon + spread},
            {"lat": lat + spread, "lng": lon + spread},
            {"lat": lat + spread, "lng": lon - spread},
        ])
        return {
            "id": f"precip-{col.get('tile_id') or stable_hash_u32(str(col))}",
            "center": {"lat": lat, "lon": lon},
            "footprint": footprint,
            "precip_type_hint": str(col.get("type_hint") or "rain"),
            "intensity_value": round(rate, 3),
            "intensity_class": cls,
            "palette_bucket": bucket,
            "source_altitude_m": float(col.get("estimated_source_altitude_m") or 0.0),
            "target_altitude_m": float(col.get("estimated_surface_altitude_m") or 0.0),
            "linked_cloud_id": str(col.get("tile_id") or ""),
            "storm_strength_hint": round(intensity, 4),
            "visual_priority": round(_clamp(intensity * 0.78 + (1 if cls in {"heavy", "very_heavy", "extreme"} else 0) * 0.18, 0.0, 1.0), 4),
            "heuristic": True,
            "source_fields": {"proxy_precip_rate": rate, "proxy_wind_u": float(col.get("wind_u") or 0.0), "proxy_wind_v": float(col.get("wind_v") or 0.0)},
        }

    def _lightning_feature_from_event(self, ev: dict[str, Any]) -> dict[str, Any]:
        energy = float(ev.get("estimated_energy") or 0.0)
        return {
            "id": f"ltg-{ev.get('tile_id') or stable_hash_u32(str(ev))}",
            "center": {"lat": float(ev.get("lat") or 0.0), "lon": float(ev.get("lon") or 0.0)},
            "path": [],
            "linked_cloud_id": str(ev.get("tile_id") or ""),
            "severity_hint": str(ev.get("severity_hint") or ("strong" if energy > 0.7 else "active")),
            "start_altitude_m": float(ev.get("estimated_flash_top_m") or 0.0),
            "end_altitude_m": float(ev.get("estimated_flash_bottom_m") or 0.0),
            "flash_hint": True,
            "visual_priority": round(_clamp(energy, 0.0, 1.0), 4),
            "heuristic": True,
            "source_fields": {"inferred_lightning_risk": energy},
        }

    def _wind_feature_from_vector(self, v: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": f"wind-{stable_hash_u32(str(v.get('lat'))+':'+str(v.get('lon'))+':'+str(v.get('source_level')))}",
            "center": {"lat": float(v.get("lat") or 0.0), "lon": float(v.get("lon") or 0.0)},
            "vector_u": float(v.get("u") or 0.0),
            "vector_v": float(v.get("v") or 0.0),
            "speed_mps": float(v.get("speed_mps") or 0.0),
            "direction_deg": float(v.get("heading_deg") or 0.0),
            "altitude_band": str(v.get("source_level") or "unknown"),
            "feature_type": "jetstream_hint",
            "visual_priority": round(_clamp(float(v.get("speed_mps") or 0.0) / 45.0, 0.0, 1.0), 4),
            "heuristic": True,
        }

    def derive_swell_features_from_wind(self, wind_features: list[dict[str, Any]], max_items: int = 120) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for w in sorted(wind_features or [], key=lambda x: float(x.get("speed_mps") or 0.0), reverse=True):
            speed = float(w.get("speed_mps") or 0.0)
            if speed < 4.0:
                continue
            out.append({
                "id": f"swell-{w.get('id')}",
                "center": dict(w.get("center") or {"lat": 0.0, "lon": 0.0}),
                "direction_deg": float(w.get("direction_deg") or 0.0),
                "height_m": round(_clamp(0.3 + speed * 0.09, 0.2, 7.0), 3),
                "period_s": round(_clamp(4.0 + speed * 0.35, 4.0, 18.0), 3),
                "sample_area_km": round(_clamp(20 + speed * 3.8, 20, 220), 2),
                "intensity_class": "high" if speed > 17 else "moderate" if speed > 10 else "low",
                "visual_priority": round(_clamp(speed / 35.0, 0.0, 1.0), 4),
                "heuristic": True,
            })
            if len(out) >= max_items:
                break
        return out

    def _fish_scene_feature(self, fish: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(fish.get("location_key") or fish.get("id") or "fish"),
            "lat": float(fish.get("lat") or 0.0),
            "lon": float(fish.get("lon") or 0.0),
            "label": str(fish.get("name") or fish.get("location_key") or "Fish"),
            "activity_hint": (fish.get("bait") or {}).get("intensity") or "unknown",
            "source": "fish_csv",
            "meta": fish.get("meta") or {},
        }

    def build_scene_payload(self, weather: dict[str, Any], bbox: dict[str, float]) -> dict[str, Any]:
        warnings: list[str] = []
        errors: list[str] = []
        clouds: list[dict[str, Any]] = []
        precip: list[dict[str, Any]] = []
        lightning: list[dict[str, Any]] = []
        wind: list[dict[str, Any]] = []
        swell: list[dict[str, Any]] = []
        fish_scene: list[dict[str, Any]] = []

        try:
            cloud_tiles = weather.get("tiles") or weather.get("items") or []
            clouds = [self._cloud_feature_from_tile(t) for t in cloud_tiles[:800] if isinstance(t, dict)]
        except Exception as exc:
            warnings.append("cloud_feature_derivation_failed")
            errors.append(str(exc))

        try:
            raw_precip = weather.get("precip_columns") or self.derive_precip_columns_from_tiles(weather.get("tiles") or weather.get("items") or [], max_items=260)
            precip = [self._precip_feature_from_column(c) for c in raw_precip if isinstance(c, dict)]
        except Exception as exc:
            warnings.append("precip_feature_derivation_failed")
            errors.append(str(exc))

        try:
            raw_ltg = weather.get("lightning_events") or self.derive_lightning_events_from_tiles(weather.get("tiles") or weather.get("items") or [], max_items=140)
            lightning = [self._lightning_feature_from_event(e) for e in raw_ltg if isinstance(e, dict)]
        except Exception as exc:
            warnings.append("lightning_feature_derivation_failed")
            errors.append(str(exc))

        try:
            raw_wind = (weather.get("balloons") or {}).get("items") if isinstance(weather.get("balloons"), dict) else []
            wind = [self._wind_feature_from_vector(v) for v in (raw_wind or []) if isinstance(v, dict)]
        except Exception as exc:
            warnings.append("wind_feature_derivation_failed")
            errors.append(str(exc))

        try:
            swell = self.derive_swell_features_from_wind(wind, max_items=120)
        except Exception as exc:
            warnings.append("swell_feature_derivation_failed")
            errors.append(str(exc))

        fish_error = None
        try:
            fish_points, fish_error = self.load_fish()
            fish_scene = [self._fish_scene_feature(f) for f in fish_points[:1500] if isinstance(f, dict)]
        except Exception as exc:
            fish_error = str(exc)
            warnings.append("fish_feature_derivation_failed")
            errors.append(str(exc))

        if fish_error:
            warnings.append("fish_source_unavailable")

        heuristic_flag = bool(weather.get("heuristic", True) or weather.get("payload_state") != "live")
        status = {
            "ok": len(errors) == 0,
            "mode": str(weather.get("payload_state") or "synthetic"),
            "warnings": warnings,
            "errors": errors,
            "upstream_available": weather.get("source") == "gfs_nomads",
            "partial": bool(warnings),
            "generated_at": self._now_ms(),
            "request_bounds": bbox,
            "fallback_active": str(weather.get("payload_state") or "synthetic") != "live",
            "heuristic_dominant": heuristic_flag,
            "decode_backend": weather.get("decode_backend") or self.state.decode_backend,
            "data_source_mode": weather.get("data_source_mode") or self.state.data_source_mode,
        }
        meta = {
            "schema_version": "atmo-scene-v1",
            "source_name": str(weather.get("source") or "fallback_proxy"),
            "source_type": "model" if weather.get("source") == "gfs_nomads" else "heuristic",
            "analysis_time": weather.get("cycle"),
            "valid_time": weather.get("valid_time"),
            "generated_at": self._now_ms(),
            "bounds": bbox,
            "units": {
                "altitude": "m",
                "wind_speed": "mps",
                "direction": "deg",
                "precip_rate": "mm_hr",
                "swell_height": "m",
                "swell_period": "s",
            },
            "heuristic_flags": {
                "scene_features_estimated": True,
                "fallback_payload": str(weather.get("payload_state") or "synthetic") != "live",
                "cloud_geometry_derived": True,
                "precip_columns_derived": True,
                "lightning_events_inferred": True,
            },
            "quality_note": weather.get("quality_note"),
            "confidence": weather.get("confidence"),
            "bbox_used": weather.get("bbox_used") or bbox,
            "source_format": weather.get("source_format") or self.state.model_source_format,
            "source_url": weather.get("source_url") or self.state.model_source_url,
            "cache_path": weather.get("cache_path") or self.state.model_cache_path,
            "fields_available": list(weather.get("fields_available") or self.state.fields_available or []),
            "fields_missing": list(weather.get("fields_missing") or self.state.fields_missing or []),
            "using_last_known_good": bool(weather.get("using_last_known_good", self.state.using_last_known_good)),
            "degraded_mode": bool(weather.get("degraded_mode", self.state.degraded_mode)),
            "decode_backend": weather.get("decode_backend") or self.state.decode_backend,
            "data_source_mode": weather.get("data_source_mode") or self.state.data_source_mode,
        }
        summary = {
            "cloud_count": len(clouds),
            "precip_count": len(precip),
            "lightning_count": len(lightning),
            "wind_count": len(wind),
            "swell_count": len(swell),
            "fish_count": len(fish_scene),
            "dominant_weather_mode": "convective" if any(float(c.get("convective_strength") or 0) > 0.65 for c in clouds[:120]) else "layered",
            "strongest_storm_class": "severe" if any(float(c.get("convective_strength") or 0) > 0.78 for c in clouds[:120]) else "moderate",
            "notes": warnings,
        }
        return {
            "status": status,
            "meta": meta,
            "scene": {
                "clouds": clouds,
                "precip": precip,
                "lightning": lightning,
                "wind": wind,
                "swell": swell,
                "fish": fish_scene,
            },
            "summary": summary,
        }
    def _degraded_scene_payload(self, bbox: dict[str, float], reason: str) -> dict[str, Any]:
        now = self._now_ms()
        return {
            "ok": False,
            "status": {
                "ok": False,
                "mode": "degraded",
                "degraded": True,
                "warnings": ["scene_generation_degraded"],
                "errors": [str(reason)],
                "partial": True,
                "generated_at": now,
                "request_bounds": bbox,
            },
            "meta": {
                "schema_version": "atmo-scene-v1",
                "generated_at": now,
                "bounds": bbox,
                "degraded": True,
                "decode_backend": self.state.decode_backend,
                "data_source_mode": self.state.data_source_mode,
            },
            "scene": {"clouds": [], "precip": [], "lightning": [], "wind": [], "swell": [], "fish": []},
            "summary": {"cloud_count": 0, "precip_count": 0, "lightning_count": 0, "wind_count": 0, "swell_count": 0, "fish_count": 0, "notes": ["degraded_response"]},
            "items": [],
            "precip_columns": [],
            "lightning_events": [],
            "source": "fallback_proxy",
            "payload_state": "degraded",
            "heuristic": True,
            "quality_note": "Degraded scene payload due to internal derivation failure.",
            "confidence": "low",
            "bbox_used": bbox,
        }

    def cloud_tiles_payload(self, bbox: dict[str, float] | None = None) -> Dict[str, Any]:
        bbox_norm = self._normalize_bbox(bbox)
        try:
            weather = self.generate_weather_payload(bbox_norm)
        except Exception as exc:
            log.exception("[gfs] scene weather generation failed")
            return self._degraded_scene_payload(bbox_norm, str(exc))

        try:
            if weather.get("source") == "gfs_nomads":
                payload = self.serialize_cloud_payload(weather.get("tiles", []), weather)
                payload["payload_state"] = weather.get("payload_state", "live")
                payload["heuristic"] = bool(weather.get("heuristic", False))
                payload["quality_note"] = weather.get("quality_note") or "Real NOMADS field ingestion with visualization-derived geometry."
                payload["confidence"] = weather.get("confidence") or "high"
                payload["rain"] = weather.get("rain", {"items": [], "count": 0})
                payload["hail"] = weather.get("hail", {"items": [], "count": 0})
                payload["lightning"] = weather.get("lightning", {"items": [], "count": 0})
                payload["balloons"] = weather.get("balloons", {"items": [], "count": 0})
                payload["precip_columns"] = self.derive_precip_columns_from_tiles(payload.get("items", []), max_items=260)
                payload["lightning_events"] = self.derive_lightning_events_from_tiles(payload.get("items", []), max_items=140)
                payload["note"] = "Primary source is NOAA NOMADS GFS 0.25 via GRIB subset decode."
                payload["cycle"] = weather.get("cycle")
                payload["forecast_hour"] = weather.get("forecast_hour")
                payload["valid_time"] = weather.get("valid_time")
                payload["bbox_used"] = weather.get("bbox_used")
            else:
                payload = self._annotate_weather_payload(
                    weather,
                    bbox=bbox_norm,
                    source=str(weather.get("source") or "fallback_proxy"),
                    payload_state=str(weather.get("payload_state") or "synthetic"),
                    heuristic=bool(weather.get("heuristic", True)),
                    quality_note=str(weather.get("quality_note") or "Synthetic weather fallback in use."),
                    confidence=str(weather.get("confidence") or "low"),
                )
                payload.setdefault("precip_columns", self.derive_precip_columns_from_tiles(payload.get("items", []), max_items=260))
                payload.setdefault("lightning_events", self.derive_lightning_events_from_tiles(payload.get("items", []), max_items=140))

            scene_payload = self.build_scene_payload(payload, bbox_norm)
            payload["status"] = scene_payload.get("status", {})
            payload["meta"] = scene_payload.get("meta", {})
            payload["scene"] = scene_payload.get("scene", {})
            payload["summary"] = scene_payload.get("summary", payload.get("summary") or {})
            payload["ok"] = bool(payload.get("ok", True) and payload["status"].get("ok", True))
            return payload
        except Exception as exc:
            log.exception("[gfs] scene payload assembly failed")
            return self._degraded_scene_payload(bbox_norm, str(exc))


    def _tile_bounds_xyz(self, z: int, x: int, y: int) -> dict[str, float]:
        z = max(0, int(z))
        n = 2 ** z
        x = max(0, min(n - 1, int(x)))
        y = max(0, min(n - 1, int(y)))

        def tile2lon(tx: int, tz: int) -> float:
            return tx / (2 ** tz) * 360.0 - 180.0

        def tile2lat(ty: int, tz: int) -> float:
            val = math.pi * (1 - 2 * ty / (2 ** tz))
            return math.degrees(math.atan(math.sinh(val)))

        west = tile2lon(x, z)
        east = tile2lon(x + 1, z)
        north = tile2lat(y, z)
        south = tile2lat(y + 1, z)
        return {"west": west, "south": south, "east": east, "north": north}

    def _expand_bounds(self, bounds: dict[str, float], pad_deg: float = 0.18) -> dict[str, float]:
        return {
            "west": float(bounds.get("west", -180.0)) - pad_deg,
            "south": float(bounds.get("south", -80.0)) - pad_deg,
            "east": float(bounds.get("east", 180.0)) + pad_deg,
            "north": float(bounds.get("north", 80.0)) + pad_deg,
        }

    def _point_in_bounds(self, lat: float, lon: float, bounds: dict[str, float]) -> bool:
        return (
            float(bounds.get("south", -90.0)) <= float(lat) <= float(bounds.get("north", 90.0)
            ) and float(bounds.get("west", -180.0)) <= float(lon) <= float(bounds.get("east", 180.0))
        )

    def _feature_intersects_bounds(self, feat: dict[str, Any], bounds: dict[str, float]) -> bool:
        center = feat.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
        if lat is not None and lon is not None and self._point_in_bounds(safe_float(lat), safe_float(lon), bounds):
            return True
        # Try legacy bounds center.
        legacy = feat.get("bounds") or {}
        if legacy:
            lat = legacy.get("lat_center")
            lon = legacy.get("lon_center")
            if lat is not None and lon is not None and self._point_in_bounds(safe_float(lat), safe_float(lon), bounds):
                return True
        # Try first footprint/path point if present.
        fp = feat.get("footprint")
        if isinstance(fp, list) and fp:
            p0 = fp[0] or {}
            lat = p0.get("lat")
            lon = p0.get("lng", p0.get("lon"))
            if lat is not None and lon is not None and self._point_in_bounds(safe_float(lat), safe_float(lon), bounds):
                return True
        return False

    def _feature_center(self, feat: dict[str, Any]) -> tuple[float | None, float | None]:
        center = feat.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")
        if lat is None or lon is None:
            b = feat.get("bounds") or {}
            lat = b.get("lat_center", lat)
            lon = b.get("lon_center", lon)
        if lat is None or lon is None:
            fp = feat.get("footprint")
            if isinstance(fp, list) and fp:
                p0 = fp[0] or {}
                lat = p0.get("lat", lat)
                lon = p0.get("lng", p0.get("lon", lon))
        lat_f = safe_float(lat, None)
        lon_f = safe_float(lon, None)
        if lat_f is None or lon_f is None:
            return None, None
        return lat_f, lon_f

    def _feature_bbox(self, feat: dict[str, Any]) -> dict[str, float]:
        b = feat.get("bbox")
        if isinstance(b, dict):
            try:
                return {"west": float(b.get("west")), "south": float(b.get("south")), "east": float(b.get("east")), "north": float(b.get("north"))}
            except Exception:
                pass
        lat, lon = self._feature_center(feat)
        if lat is None or lon is None:
            return {"west": -181.0, "south": -91.0, "east": -181.0, "north": -91.0}
        return {"west": lon, "south": lat, "east": lon, "north": lat}

    def _grid_cells_for_bbox(self, bbox: dict[str, float], cell_deg: float = SPATIAL_GRID_DEG) -> list[str]:
        west = float(bbox.get("west", -180.0))
        east = float(bbox.get("east", 180.0))
        south = float(bbox.get("south", -90.0))
        north = float(bbox.get("north", 90.0))
        gx0 = int(math.floor((west + 180.0) / cell_deg))
        gx1 = int(math.floor((east + 180.0) / cell_deg))
        gy0 = int(math.floor((south + 90.0) / cell_deg))
        gy1 = int(math.floor((north + 90.0) / cell_deg))
        cells = []
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                cells.append(f"{gx}:{gy}")
        return cells

    def _dedup_key(self, layer: str, feat: dict[str, Any], meta: dict[str, Any]) -> str:
        if layer == "lightning":
            c = meta.get("centroid") or {}
            lat = round(safe_float(c.get("lat"), 0.0), 2)
            lon = round(safe_float(c.get("lon"), 0.0), 2)
            t = str(meta.get("time_bucket") or "na")
            intensity = round(safe_float(feat.get("estimated_energy") or feat.get("intensity") or feat.get("severity"), 0.0), 2)
            kind = str(feat.get("type") or feat.get("kind") or "strike")
            return f"ltg:{lat}:{lon}:{t}:{intensity}:{kind}"
        return str(meta.get("feature_id") or feat.get("id") or stable_hash_u32(json.dumps(feat, sort_keys=True, default=str)))

    def _feature_meta(self, layer: str, feat: dict[str, Any]) -> dict[str, Any]:
        lat, lon = self._feature_center(feat)
        bbox = self._feature_bbox(feat)
        fid = str(feat.get("id") or f"{layer}-{stable_hash_u32(json.dumps(feat, sort_keys=True, default=str))}")
        time_bucket = str(feat.get("time_bucket") or feat.get("ts_bucket") or feat.get("valid_time") or "na")
        altitude_band = str(feat.get("altitude_band") or feat.get("band") or "surface")
        meta = {
            "feature_id": fid,
            "layer": layer,
            "bbox": bbox,
            "centroid": {"lat": lat, "lon": lon},
            "time_bucket": time_bucket,
            "altitude_band": altitude_band,
        }
        meta["dedup_key"] = self._dedup_key(layer, feat, meta)
        return meta

    def _intersects_bbox(self, a: dict[str, float], b: dict[str, float]) -> bool:
        return not (a.get("east", -999) < b.get("west", 999) or a.get("west", 999) > b.get("east", -999) or a.get("north", -999) < b.get("south", 999) or a.get("south", 999) > b.get("north", -999))

    def _build_layer_feature_indexes(self, scene_payload: dict[str, Any]) -> None:
        scene = scene_payload.get("scene") if isinstance(scene_payload.get("scene"), dict) else {}
        layer_sources = {
            "clouds": scene_payload.get("items") or [],
            "precip": scene_payload.get("precip_columns") or scene.get("precip") or [],
            "lightning": scene_payload.get("lightning_events") or scene.get("lightning") or [],
            "wind": scene.get("wind") or ((scene_payload.get("balloons") or {}).get("items") if isinstance(scene_payload.get("balloons"), dict) else []) or [],
            "swell": scene.get("swell") or [],
            "sst": scene.get("sst") or [],
            "fish": self.fish_payload().get("items") or [],
        }
        self.state.layer_feature_index = {}
        self.state.layer_feature_meta = {}
        self.state.layer_feature_store = {}
        for layer, feats in layer_sources.items():
            idx = {}
            metas = {}
            store = {}
            for feat in feats:
                if not isinstance(feat, dict):
                    continue
                meta = self._feature_meta(layer, feat)
                fid = meta["feature_id"]
                metas[fid] = meta
                store[fid] = feat
                for cell in self._grid_cells_for_bbox(meta["bbox"]):
                    idx.setdefault(cell, set()).add(fid)
            self.state.layer_feature_index[layer] = idx
            self.state.layer_feature_meta[layer] = metas
            self.state.layer_feature_store[layer] = store

    def _spatial_candidates(self, layer: str, bounds: dict[str, float]) -> tuple[list[dict[str, Any]], int]:
        idx = self.state.layer_feature_index.get(layer) or {}
        metas = self.state.layer_feature_meta.get(layer) or {}
        store = self.state.layer_feature_store.get(layer) or {}
        if not idx:
            return [], 0
        candidate_ids = set()
        for cell in self._grid_cells_for_bbox(bounds):
            candidate_ids.update(idx.get(cell) or set())
        out = []
        for fid in candidate_ids:
            meta = metas.get(fid) or {}
            if not self._intersects_bbox(meta.get("bbox") or {}, bounds):
                continue
            feat = store.get(fid)
            if feat is None:
                continue
            f = dict(feat)
            f.setdefault("feature_id", fid)
            f.setdefault("layer", layer)
            f.setdefault("bbox", meta.get("bbox"))
            f.setdefault("centroid", meta.get("centroid"))
            f.setdefault("time_bucket", meta.get("time_bucket"))
            f.setdefault("altitude_band", meta.get("altitude_band"))
            f.setdefault("dedup_key", meta.get("dedup_key"))
            c = f.get("centroid") or {}
            lat = safe_float(c.get("lat"), None)
            lon = safe_float(c.get("lon"), None)
            if lat is None or lon is None:
                log.debug("[gfs] skip malformed feature without finite centroid layer=%s fid=%s", layer, fid)
                continue
            out.append(f)
        return out, len(candidate_ids)

    def _record_tile_diag(self, key: str, diag: dict[str, Any]) -> None:
        self.state.tile_diagnostics[key] = diag
        if len(self.state.tile_diagnostics) > MAX_TILE_DIAGNOSTICS:
            for old_key in sorted(self.state.tile_diagnostics.keys())[: len(self.state.tile_diagnostics) - MAX_TILE_DIAGNOSTICS]:
                self.state.tile_diagnostics.pop(old_key, None)

    def _scene_cache_key(self, bbox: dict[str, float]) -> str:
        return f"scene:{round(safe_float(bbox.get('west'),-180.0),3)}:{round(safe_float(bbox.get('south'),-80.0),3)}:{round(safe_float(bbox.get('east'),180.0),3)}:{round(safe_float(bbox.get('north'),80.0),3)}"

    def _get_cached_scene_payload(self, bbox: dict[str, float]) -> tuple[dict[str, Any], dict[str, Any]]:
        now_ms = self._now_ms()
        ttl_ms = max(10_000, int((getattr(self.state, "tile_cache_ttl_seconds", 30) or 30) * 1000))
        key = self._scene_cache_key(bbox)
        row = self.state.tile_cache.get(key) or {}
        diag = {"scene_cache_key": key, "cache_hit": False, "cache_age_ms": None, "build_duration_ms": 0}
        if row and (now_ms - int(row.get("ts") or 0)) <= ttl_ms and isinstance(row.get("payload"), dict):
            diag["cache_hit"] = True
            diag["cache_age_ms"] = now_ms - int(row.get("ts") or 0)
            return row["payload"], diag
        started = time.perf_counter()
        try:
            payload = self.cloud_tiles_payload(bbox)
        except Exception as exc:
            log.exception("[gfs] cached scene generation failed")
            payload = self._degraded_scene_payload(bbox, str(exc))
        diag["build_duration_ms"] = int((time.perf_counter() - started) * 1000)
        self.state.tile_cache[key] = {"ts": now_ms, "payload": payload}
        try:
            self._build_layer_feature_indexes(payload)
        except Exception:
            log.exception("[gfs] layer feature index build failed")
        return payload, diag

    def layer_tile_payload(self, layer: str, z: int, x: int, y: int, pad_deg: float = 0.18, debug: bool = False) -> dict[str, Any]:
        layer_name = (layer or "").strip().lower()
        tile_bounds = self._tile_bounds_xyz(z, x, y)
        filter_bounds = self._expand_bounds(tile_bounds, pad_deg=pad_deg)
        tile_key = f"{layer_name}:{z}/{x}/{y}"
        scene_payload, cache_diag = self._get_cached_scene_payload(filter_bounds)
        if not self.state.layer_feature_index.get(layer_name):
            self._build_layer_feature_indexes(scene_payload)
        scene = scene_payload.get("scene") if isinstance(scene_payload.get("scene"), dict) else {}

        caps = {"clouds": 220, "precip": 240, "lightning": 160, "wind": 220, "swell": 160, "sst": 180, "fish": 260}
        if layer_name not in caps:
            return {
                "status": {"ok": False, "mode": "invalid", "errors": ["unknown_layer"], "warnings": [], "partial": False},
                "meta": {"layer": layer_name, "z": z, "x": x, "y": y, "bounds": tile_bounds, "schema_version": "atmo-tile-v1", "generated_at": self._now_ms()},
                "features": [],
                "summary": {"count": 0},
            }

        try:
            candidates, candidate_count = self._spatial_candidates(layer_name, filter_bounds)
            precise = [f for f in candidates if self._feature_intersects_bounds(f, filter_bounds)]
        except Exception as exc:
            log.exception("[gfs] tile derivation failed layer=%s tile=%s/%s/%s", layer_name, z, x, y)
            candidates, candidate_count, precise = [], 0, []
        precise = sorted(precise, key=lambda f: safe_float(f.get("visual_priority", f.get("importance", 0.0)), 0.0), reverse=True)

        dedup = []
        dedup_seen = set()
        dedup_suppressed = 0
        for feat in precise:
            dkey = str(feat.get("dedup_key") or feat.get("feature_id") or feat.get("id") or stable_hash_u32(json.dumps(feat, sort_keys=True, default=str)))
            if dkey in dedup_seen:
                dedup_suppressed += 1
                continue
            dedup_seen.add(dkey)
            dedup.append(feat)
        features = dedup[: caps[layer_name]]

        if layer_name in {"precip", "clouds", "sst"}:
            field_map = {"precip": "precip_rate", "clouds": "cloud_density", "sst": "temperature_k"}
            contour_geo = self._marching_squares_contours(field_map[layer_name], filter_bounds, z)
            for feat in contour_geo:
                features.append({
                    "id": f"contour-{stable_hash_u32(json.dumps(feat, sort_keys=True, default=str))}",
                    "type": "contour",
                    "geojson": feat,
                    "visual_priority": 0.4,
                })

        src_status = scene_payload.get("status") if isinstance(scene_payload.get("status"), dict) else {}
        src_meta = scene_payload.get("meta") if isinstance(scene_payload.get("meta"), dict) else {}
        diagnostics = {
            "tile_key": tile_key,
            "layer": layer_name,
            "request_bounds": tile_bounds,
            "filter_bounds": filter_bounds,
            "cache_hit": cache_diag.get("cache_hit", False),
            "cache_age_ms": cache_diag.get("cache_age_ms"),
            "build_duration_ms": cache_diag.get("build_duration_ms", 0),
            "candidate_count": candidate_count,
            "precise_count": len(precise),
            "emitted_count": len(features),
            "dedup_suppressed_count": dedup_suppressed,
            "decode_backend": self.state.decode_backend,
            "data_source_mode": self.state.data_source_mode,
            "cycle": self.state.model_cycle,
            "forecast_hour": self.state.model_forecast_hour,
            "valid_time": self.state.model_valid_time,
        }
        self._record_tile_diag(tile_key, diagnostics)

        out = {
            "status": {
                "ok": bool(src_status.get("ok", True)),
                "mode": str(src_status.get("mode") or scene_payload.get("payload_state") or "live"),
                "warnings": list(src_status.get("warnings") or []),
                "errors": list(src_status.get("errors") or []),
                "partial": bool(src_status.get("partial", False)),
                "generated_at": self._now_ms(),
                "request_bounds": tile_bounds,
                "data_source_mode": self.state.data_source_mode,
                "decode_backend": self.state.decode_backend,
            },
            "meta": {
                "layer": layer_name,
                "z": int(z),
                "x": int(x),
                "y": int(y),
                "bounds": tile_bounds,
                "filter_bounds": filter_bounds,
                "schema_version": "atmo-tile-v1",
                "generated_at": self._now_ms(),
                "analysis_time": src_meta.get("analysis_time") or scene_payload.get("cycle"),
                "valid_time": src_meta.get("valid_time") or scene_payload.get("valid_time"),
                "heuristic": bool(src_meta.get("heuristic_flags", {}).get("scene_features_estimated", scene_payload.get("heuristic", True))),
                "decode_backend": self.state.decode_backend,
                "data_source_mode": self.state.data_source_mode,
            },
            "features": features,
            "summary": {
                "count": len(features),
                "layer": layer_name,
                "tile": f"{z}/{x}/{y}",
                "candidate_count": candidate_count,
                "dedup_suppressed": dedup_suppressed,
            },
        }
        if debug:
            out["diagnostics"] = diagnostics
        return out

    def tile_diagnostics_payload(self, layer: str | None = None, tile: str | None = None) -> dict[str, Any]:
        layer_name = (layer or "").strip().lower()
        rows = []
        for key, diag in sorted(self.state.tile_diagnostics.items(), key=lambda kv: kv[0]):
            if layer_name and diag.get("layer") != layer_name:
                continue
            if tile and tile not in key:
                continue
            rows.append(diag)
        return {
            "status": {"ok": True, "generated_at": self._now_ms()},
            "meta": {
                "schema_version": "tile-diagnostics-v1",
                "decode_backend": self.state.decode_backend,
                "data_source_mode": self.state.data_source_mode,
                "cycle": self.state.model_cycle,
                "forecast_hour": self.state.model_forecast_hour,
            },
            "items": rows[-250:],
            "summary": {"count": len(rows[-250:])},
        }

    def _load_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {"locations": {}}
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return {"locations": {}}

    def _save_store(self, data: Dict[str, Any]) -> None:
        self.store_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _ensure_location_record(self, store: Dict[str, Any], location_key: str) -> Dict[str, Any]:
        locations = store.setdefault("locations", {})
        rec = locations.setdefault(
            location_key,
            {
                "location_key": location_key,
                "report_text": "",
                "report_updated_at": None,
                "live": {"active": False, "stream_url": "", "updated_at": None},
                "uploads": [],
            },
        )
        return rec

    def _location_media_payload(self, location_key: str) -> Dict[str, Any]:
        store = self._load_store()
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key", "location_key": "", "uploads": [], "live": {}, "report_text": ""}
        rec = self._ensure_location_record(store, key)
        fish_match = next((p for p in self.state.fish_points if p.get("location_key") == key), None)
        now_ms = self._now_ms()
        if fish_match:
            intel = self._build_bait_intel(fish_match, now_ms)
        else:
            intel = self._heuristic_context(None, None, now_ms)
        return {
            "ok": True,
            "location_key": key,
            "label": fish_match.get("name") if fish_match else key,
            "report_text": rec.get("report_text") or "",
            "report_updated_at": rec.get("report_updated_at"),
            "uploads": rec.get("uploads") or [],
            "live": rec.get("live") or {"active": False, "stream_url": "", "updated_at": None},
            **intel,
            "ts": self._now_ms(),
        }

    def health(self) -> Dict[str, Any]:
        points, _ = self.load_fish()
        return {
            "ok": True,
            "enabled": self.state.enabled,
            "source": self.state.source_name,
            "fish_count": len(points),
            "last_refresh_ts": self.state.last_refresh_ts,
            "last_error": self.state.last_error,
            "fish_csv": str(self._fish_csv_path()),
            "ingest": {
                "status": self.state.ingest_status,
                "last_attempt_ts": self.state.ingest_last_attempt_ts,
                "last_success_ts": self.state.ingest_last_success_ts,
                "error": self.state.ingest_error,
                "degraded_mode": self.state.degraded_mode,
                "using_last_known_good": self.state.using_last_known_good,
                "cycle": self.state.model_cycle,
                "forecast_hour": self.state.model_forecast_hour,
                "valid_time": self.state.model_valid_time,
                "analysis_time": self.state.model_analysis_time,
                "source_url": self.state.model_source_url,
                "cache_path": self.state.model_cache_path,
                "source_format": self.state.model_source_format,
                "fields_available": list(self.state.fields_available or []),
                "fields_missing": list(self.state.fields_missing or []),
                "decode_backend": self.state.decode_backend,
                "data_source_mode": self.state.data_source_mode,
            },
            "ts": self._now_ms(),
        }

    def config(self) -> Dict[str, Any]:
        return {
            "enabled": self.state.enabled,
            "api_base": "/gfs/api",
            "ws_base": "/gfs/ws",
            "cache_ttl_seconds": self.state.cache_ttl_seconds,
            "ingest": {
                "fallback_cycle_depth": INGEST_FALLBACK_CYCLE_DEPTH,
                "preferred_forecast_hour": INGEST_PREFERRED_FORECAST_HOUR,
                "cache_min_bytes": INGEST_CACHE_MIN_BYTES,
                "source_format": "grib2",
            },
            "ts": self._now_ms(),
        }

    def load_fish(self) -> Tuple[List[Dict[str, Any]], str | None]:
        csv_path = self._fish_csv_path()
        if not csv_path.exists():
            self.state.last_error = f"missing fish CSV: {csv_path}"
            return [], self.state.last_error

        points: List[Dict[str, Any]] = []
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if not row:
                        continue

                    lat_raw = row.get("lat") or row.get("latitude") or row.get("Lat") or row.get("Latitude")
                    lon_raw = row.get("lon") or row.get("lng") or row.get("longitude") or row.get("Lon") or row.get("Longitude")
                    if lat_raw is None or lon_raw is None:
                        continue

                    try:
                        lat = float(str(lat_raw).strip())
                        lon = float(str(lon_raw).strip())
                    except Exception:
                        continue

                    name = (row.get("name") or row.get("location") or row.get("label") or f"Location {i + 1}").strip()
                    location_key = self._normalize_location_key((row.get("location_key") or name or str(i + 1)))
                    if not location_key:
                        location_key = f"loc-{i+1}"
                    point = {
                        "id": (row.get("id") or row.get("locationId") or str(i + 1)).strip(),
                        "location_key": location_key,
                        "name": name,
                        "lat": lat,
                        "lon": lon,
                        "meta": {
                            k: v
                            for k, v in row.items()
                            if k
                            not in {
                                "lat",
                                "latitude",
                                "Lat",
                                "Latitude",
                                "lon",
                                "lng",
                                "longitude",
                                "Lon",
                                "Longitude",
                                "name",
                                "location",
                                "label",
                                "id",
                                "locationId",
                                "location_key",
                            }
                        },
                    }
                    point.update(self._build_bait_intel(point, self._now_ms()))
                    points.append(point)

            self.state.fish_points = points
            self.state.last_refresh_ts = self._now_ms()
            self.state.last_error = None
            return points, None
        except Exception as exc:  # noqa: BLE001
            self.state.last_error = f"failed to parse fish CSV: {exc}"
            return [], self.state.last_error

    def fish_payload(self) -> Dict[str, Any]:
        points, err = self.load_fish()
        return {"ok": err is None, "error": err, "count": len(points), "items": points, "ts": self._now_ms()}

    def frame_payload(self) -> Dict[str, Any]:
        points, _ = self.load_fish()
        now_ms = self._now_ms()
        if points:
            seed_point = points[0]
        else:
            seed_point = {
                "location_key": "world-center",
                "lat": DEFAULT_WORLD_ENV_MARKER["lat"],
                "lon": DEFAULT_WORLD_ENV_MARKER["lon"],
                "meta": {},
            }
        intel = self._build_bait_intel(seed_point, now_ms)
        env = intel.get("environment", {})
        return {
            "ok": True,
            "model": "GFS",
            "frame": {
                "wind_knots": env.get("wind_speed_kt", 0),
                "wave_feet": env.get("wave_feet", 0),
                "cloud_pct": env.get("cloud_cover_pct", 0),
                "current_knots": env.get("current_speed_kt", 0),
                "sst_f": env.get("sst_f"),
                "updated_at": now_ms,
            },
        }

    def contours_payload(self) -> Dict[str, Any]:
        return {"ok": True, "features": [], "ts": self._now_ms()}

    def overlay_payload(self) -> Dict[str, Any]:
        return {"ok": True, "layers": [], "ts": self._now_ms()}

    def legend_payload(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "legend": [
                {"name": "Low", "color": "#1f77b4"},
                {"name": "Medium", "color": "#ff7f0e"},
                {"name": "High", "color": "#d62728"},
            ],
            "ts": self._now_ms(),
        }

    def tile_png_bytes(self, z: int, x: int, y: int) -> bytes:
        _ = (z, x, y)
        return base64.b64decode(_TRANSPARENT_PNG_BASE64)

    def location_media(self, location_key: str) -> Dict[str, Any]:
        return self._location_media_payload(location_key)

    def upsert_report(self, location_key: str, report_text: str) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        rec["report_text"] = (report_text or "").strip()
        rec["report_updated_at"] = self._now_ms()
        self._save_store(store)
        return {"ok": True, **self._location_media_payload(key)}

    def upsert_live(self, location_key: str, active: bool, stream_url: str) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        rec["live"] = {
            "active": bool(active),
            "stream_url": (stream_url or "").strip(),
            "updated_at": self._now_ms(),
        }
        self._save_store(store)
        return {"ok": True, **self._location_media_payload(key)}

    def save_upload_video(self, location_key: str, filename: str, raw: bytes) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        if not raw:
            return {"ok": False, "error": "empty upload"}

        safe_name = secure_filename(filename or "upload.mp4")
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_VIDEO_EXTS:
            return {"ok": False, "error": f"unsupported file type: {ext or 'none'}"}

        saved_name = f"{key}-{self._now_ms()}{ext}"
        out_path = self.fishvid_dir / saved_name
        out_path.write_bytes(raw)
        media_url = f"/static/fishvid/{saved_name}"

        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        uploads = rec.setdefault("uploads", [])
        uploads.insert(
            0,
            {
                "url": media_url,
                "filename": saved_name,
                "mime": f"video/{ext.lstrip('.') if ext != '.mov' else 'quicktime'}",
                "uploaded_at": self._now_ms(),
            },
        )
        rec["uploads"] = uploads[:20]
        self._save_store(store)
        return {"ok": True, "location_key": key, "media_url": media_url, **self._location_media_payload(key)}

    async def ws_snapshot(self) -> Dict[str, Any]:
        cloud_payload = self.cloud_tiles_payload()
        return {
            "type": "gfs_update",
            "health": self.health(),
            "frame": self.frame_payload().get("frame", {}),
            "clouds": {
                "updated_at": cloud_payload.get("updated_at"),
                "summary": cloud_payload.get("summary", {}),
                "source": cloud_payload.get("source"),
            },
            "ts": self._now_ms(),
        }
