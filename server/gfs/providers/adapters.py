from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from server.gfs.models import BBox


@dataclass(frozen=True)
class Viewport:
    west: float
    south: float
    east: float
    north: float


@dataclass(frozen=True)
class ErddapSlice:
    lon_start: float
    lon_stop: float
    lat_start: float
    lat_stop: float


def _iso_time_or_last(valid_time: datetime | None) -> str:
    return f"{valid_time.isoformat()}Z" if valid_time else "last"


def normalize_lon(lon: float, convention: str) -> float:
    if convention == "0360":
        while lon < 0.0:
            lon += 360.0
        while lon >= 360.0:
            lon -= 360.0
        return lon
    while lon < -180.0:
        lon += 360.0
    while lon >= 180.0:
        lon -= 360.0
    return lon


def split_antimeridian(viewport: Viewport) -> list[ErddapSlice]:
    # ERDDAP lon slices cannot cross the antimeridian in one interval.
    if viewport.west <= viewport.east:
        return [ErddapSlice(viewport.west, viewport.east, viewport.south, viewport.north)]
    return [
        ErddapSlice(viewport.west, 180.0, viewport.south, viewport.north),
        ErddapSlice(-180.0, viewport.east, viewport.south, viewport.north),
    ]


def build_ncss_subset_request(viewport: Viewport, vars: list[str], stride: int, valid_time: datetime | None, base_url: str) -> str:
    query: list[tuple[str, str]] = [
        ("north", str(viewport.north)),
        ("south", str(viewport.south)),
        ("west", str(viewport.west)),
        ("east", str(viewport.east)),
        ("time", "present" if valid_time is None else _iso_time_or_last(valid_time)),
        ("horizStride", str(max(1, int(stride)))),
        ("accept", "netCDF4"),
        ("addLatLon", "true"),
    ]
    for v in vars:
        query.append(("var", v))
    return f"{base_url}?{urllib.parse.urlencode(query)}"


def build_erddap_subset_request(
    viewport: Viewport,
    dataset_csv_url: str,
    vars: list[str],
    stride: int,
    valid_time: datetime | None,
    *,
    lon_convention: str = "pm180",
) -> list[str]:
    time_selector = _iso_time_or_last(valid_time)
    stride_val = max(1, int(stride))
    if lon_convention == "0360":
        lon_view = Viewport(
            west=normalize_lon(viewport.west, "0360"),
            south=viewport.south,
            east=normalize_lon(viewport.east, "0360"),
            north=viewport.north,
        )
    else:
        lon_view = Viewport(
            west=normalize_lon(viewport.west, "pm180"),
            south=viewport.south,
            east=normalize_lon(viewport.east, "pm180"),
            north=viewport.north,
        )
    urls: list[str] = []
    for s in split_antimeridian(lon_view):
        for var in vars:
            constraint = f"[{time_selector}][({s.lat_start}):{stride_val}:({s.lat_stop})][({s.lon_start}):{stride_val}:({s.lon_stop})]"
            params = urllib.parse.urlencode({var: constraint})
            urls.append(f"{dataset_csv_url}?{params}")
    return urls


def build_station_enrichment_request(viewport: Viewport, valid_time: datetime | None) -> dict[str, Any]:
    # Station APIs are not bbox-grid sources: use viewport center for nearest-station enrichment only.
    center_lat = (viewport.south + viewport.north) * 0.5
    center_lon = (viewport.west + viewport.east) * 0.5
    return {
        "center_lat": center_lat,
        "center_lon": center_lon,
        "valid_time": _iso_time_or_last(valid_time),
    }


def viewport_from_bbox(bbox: BBox) -> Viewport:
    return Viewport(west=bbox.west, south=bbox.south, east=bbox.east, north=bbox.north)
