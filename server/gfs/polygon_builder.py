from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from server.gfs.normalize import normalize_lon_deg, to_number
from server.gfs.serializers import iso_utc


SCHEMA_NAME = "gfs_polygon_field_v1"


def _as_2d(grid: Any) -> list[list[float]]:
    if not isinstance(grid, list) or not grid:
        return []
    if isinstance(grid[0], list) and grid[0] and isinstance(grid[0][0], list):
        return grid[0]
    if isinstance(grid[0], list):
        return grid
    return []


def _cell_lat_lon(i: int, j: int, ny: int, nx: int, bbox: list[float]) -> tuple[float, float]:
    west, south, east, north = bbox
    lat = south + ((i + 0.5) / max(1, ny)) * (north - south)
    lon = west + ((j + 0.5) / max(1, nx)) * (east - west)
    return lat, normalize_lon_deg(lon)


def _source_time_text(source_time: datetime | None) -> str | None:
    return iso_utc(source_time)


def _finite_number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(n):
        return None
    return n


def _is_believable_ocean_cell(*, sst: float | None, chlorophyll: float | None, current_u: float | None, current_v: float | None, ssh_anomaly: float | None) -> bool:
    if sst is not None and -2.5 <= sst <= 38.5:
        return True
    if chlorophyll is not None and chlorophyll > 0.0:
        return True
    if current_u is not None and current_v is not None and math.hypot(current_u, current_v) >= 0.03:
        return True
    if ssh_anomaly is not None and abs(ssh_anomaly) >= 0.01:
        return True
    return False


def build_polygon_field_v1_from_atmos(
    *,
    layer: str,
    bbox: list[float],
    cell_size_deg: float,
    source_time: datetime | None,
    atmos: dict[str, Any],
    altitude_base_m: int = 0,
    quality: str | None = None,
    include_fields: tuple[str, ...] | None = None,
    max_count: int = 1500,
) -> dict[str, Any]:
    fields_2d = {
        "wind_u": _as_2d(atmos.get("wind_u", [])),
        "wind_v": _as_2d(atmos.get("wind_v", [])),
        "air_temp": _as_2d(atmos.get("air_temp", [])),
        "rel_humidity": _as_2d(atmos.get("rel_humidity", [])),
        "dewpoint": _as_2d(atmos.get("dewpoint", [])),
        "pressure_msl": _as_2d(atmos.get("pressure_msl", [])),
        "precip_rate": _as_2d(atmos.get("precip_rate", [])),
        "cloud_total": _as_2d(atmos.get("cloud_total", [])),
        "cloud_low": _as_2d(atmos.get("cloud_low", [])),
        "cloud_mid": _as_2d(atmos.get("cloud_mid", [])),
        "cloud_high": _as_2d(atmos.get("cloud_high", [])),
    }

    base = next((g for g in fields_2d.values() if g), [])

    dynamic_keys = include_fields or (
        "wind_u",
        "wind_v",
        "air_temp",
        "rel_humidity",
        "dewpoint",
        "pressure_msl",
        "precip_rate",
        "cloud_total",
        "cloud_low",
        "cloud_mid",
        "cloud_high",
    )
    output_keys = ("lat", "lon", "altitude_m", *dynamic_keys)
    if not base:
        return {
            "schema": SCHEMA_NAME,
            "layer": layer,
            "bbox": bbox,
            "cell_size_deg": cell_size_deg,
            "source_time": _source_time_text(source_time),
            "count": 0,
            "quality": quality,
            "fields": {k: [] for k in output_keys},
        }

    ny = len(base)
    nx = len(base[0]) if ny else 0
    step = max(1, int(max(nx, ny) / 42))

    out: dict[str, list[float]] = {k: [] for k in output_keys}

    def read(grid: list[list[float]], i: int, j: int) -> float:
        if i >= len(grid) or j >= len(grid[i]):
            return 0.0
        return to_number(grid[i][j], 0.0)

    for i in range(0, ny, step):
        for j in range(0, nx, step):
            lat, lon = _cell_lat_lon(i, j, ny, nx, bbox)
            out["lat"].append(round(lat, 5))
            out["lon"].append(round(lon, 5))
            out["altitude_m"].append(float(altitude_base_m))
            for key in dynamic_keys:
                out[key].append(round(read(fields_2d.get(key, []), i, j), 4))
            if len(out["lat"]) >= max_count:
                break
        if len(out["lat"]) >= max_count:
            break

    return {
        "schema": SCHEMA_NAME,
        "layer": layer,
        "bbox": bbox,
        "cell_size_deg": cell_size_deg,
        "source_time": _source_time_text(source_time),
        "count": len(out["lat"]),
        "quality": quality,
        "fields": out,
    }



def build_bait_base_field_v1(*, bbox: list[float], cell_size_deg: float, source_time: datetime | None, atmos: dict[str, Any], altitude_base_m: int = 0, quality: str | None = None, max_count: int = 1500) -> dict[str, Any]:
    payload = build_polygon_field_v1_from_atmos(
        layer="bait_base",
        bbox=bbox,
        cell_size_deg=cell_size_deg,
        source_time=source_time,
        atmos=atmos,
        altitude_base_m=altitude_base_m,
        quality=quality,
        include_fields=("wind_u", "wind_v", "air_temp", "rel_humidity", "dewpoint", "pressure_msl", "precip_rate", "cloud_total"),
        max_count=max_count,
    )
    payload["schema"] = "gfs_bait_field_v1"
    return payload


def build_bait_ocean_field_v1(*, bbox: list[float], cell_size_deg: float, source_time: datetime | None, ocean: dict[str, Any], quality: str | None = None, max_count: int = 1500) -> dict[str, Any]:
    sst = _as_2d(ocean.get("sst", []))
    chla = _as_2d(ocean.get("chlorophyll", []))
    cu = _as_2d(ocean.get("current_u", []))
    cv = _as_2d(ocean.get("current_v", []))
    wci = _as_2d(ocean.get("water_color_index", []))
    ssh = _as_2d(ocean.get("optional_ssh_anomaly", []))
    base = sst or chla or cu or cv or wci or ssh
    out = {
        "lat": [],
        "lon": [],
        "sst": [],
        "chlorophyll": [],
        "current_u": [],
        "current_v": [],
        "water_color_index": [],
        "optional_ssh_anomaly": [],
    }
    if not base:
        return {
            "schema": "bait_ocean_field_v1",
            "layer": "bait_advanced",
            "bbox": bbox,
            "cell_size_deg": cell_size_deg,
            "source_time": _source_time_text(source_time),
            "count": 0,
            "quality": quality,
            "fields": out,
        }

    ny = len(base)
    nx = len(base[0]) if ny else 0
    step = max(1, int(max(nx, ny) / 42))

    def read(grid: list[list[float]], i: int, j: int) -> float | None:
        if i >= len(grid) or j >= len(grid[i]):
            return None
        return _finite_number(grid[i][j])

    for i in range(0, ny, step):
        for j in range(0, nx, step):
            lat, lon = _cell_lat_lon(i, j, ny, nx, bbox)
            if not math.isfinite(lat) or not math.isfinite(lon):
                continue

            sst_v = read(sst, i, j)
            chla_v = read(chla, i, j)
            cu_v = read(cu, i, j)
            cv_v = read(cv, i, j)
            wci_v = read(wci, i, j)
            ssh_v = read(ssh, i, j)

            if not _is_believable_ocean_cell(
                sst=sst_v,
                chlorophyll=chla_v,
                current_u=cu_v,
                current_v=cv_v,
                ssh_anomaly=ssh_v,
            ):
                continue

            out["lat"].append(round(lat, 5))
            out["lon"].append(round(lon, 5))
            out["sst"].append(round(sst_v if sst_v is not None else 0.0, 4))
            out["chlorophyll"].append(round(chla_v if chla_v is not None else 0.0, 4))
            out["current_u"].append(round(cu_v if cu_v is not None else 0.0, 4))
            out["current_v"].append(round(cv_v if cv_v is not None else 0.0, 4))
            out["water_color_index"].append(round(wci_v if wci_v is not None else 0.0, 4))
            out["optional_ssh_anomaly"].append(round(ssh_v if ssh_v is not None else 0.0, 4))
            if len(out["lat"]) >= max_count:
                break
        if len(out["lat"]) >= max_count:
            break

    return {
        "schema": "bait_ocean_field_v1",
        "layer": "bait_advanced",
        "bbox": bbox,
        "cell_size_deg": cell_size_deg,
        "source_time": _source_time_text(source_time),
        "count": len(out["lat"]),
        "quality": quality,
        "fields": out,
    }
