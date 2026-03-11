from __future__ import annotations

from dataclasses import dataclass

from server.gfs.errors import InvalidBBoxError
from server.gfs.models import BBox


@dataclass(frozen=True)
class BBoxNormalized:
    parts: tuple[BBox, ...]
    merged: BBox
    stride: int


def parse_bbox_param(raw: str) -> BBox:
    try:
        west, south, east, north = [float(part.strip()) for part in raw.split(",")]
    except Exception as exc:
        raise InvalidBBoxError("bbox must be west,south,east,north") from exc
    if south >= north:
        raise InvalidBBoxError("bbox latitude bounds are invalid")
    if west == east:
        raise InvalidBBoxError("bbox longitude bounds are invalid")
    return BBox(west=west, south=south, east=east, north=north)


def _clamp(v: float, low: float, high: float) -> float:
    return max(low, min(high, v))


def expand_and_clamp(bbox: BBox, pad: float) -> BBox:
    lon_span = abs(bbox.east - bbox.west)
    lat_span = abs(bbox.north - bbox.south)
    lon_pad = lon_span * pad
    lat_pad = lat_span * pad
    west = _clamp(bbox.west - lon_pad, -180.0, 180.0)
    east = _clamp(bbox.east + lon_pad, -180.0, 180.0)
    south = _clamp(bbox.south - lat_pad, -90.0, 90.0)
    north = _clamp(bbox.north + lat_pad, -90.0, 90.0)
    return BBox(west=west, south=south, east=east, north=north)


def split_dateline(bbox: BBox) -> tuple[BBox, ...]:
    if bbox.west <= bbox.east:
        return (bbox,)
    return (
        BBox(west=bbox.west, south=bbox.south, east=180.0, north=bbox.north),
        BBox(west=-180.0, south=bbox.south, east=bbox.east, north=bbox.north),
    )


def estimate_cell_count(bbox: BBox, resolution_deg: float = 0.25) -> int:
    lon_span = bbox.east - bbox.west if bbox.east >= bbox.west else (180 - bbox.west) + (bbox.east + 180)
    lat_span = bbox.north - bbox.south
    nx = max(1, int(lon_span / resolution_deg))
    ny = max(1, int(lat_span / resolution_deg))
    return nx * ny


def choose_stride(cell_count: int, max_cells: int) -> int:
    stride = 1
    while (cell_count / (stride * stride)) > max_cells:
        stride += 1
    return stride


def normalize_bbox(raw_bbox: str, pad: float, max_pad: float, max_cells: int) -> BBoxNormalized:
    if pad < 0 or pad > max_pad:
        raise InvalidBBoxError(f"pad must be between 0 and {max_pad}")
    parsed = parse_bbox_param(raw_bbox)
    expanded = expand_and_clamp(parsed, pad)
    parts = split_dateline(expanded)
    cell_count = sum(estimate_cell_count(part) for part in parts)
    stride = choose_stride(cell_count=cell_count, max_cells=max_cells)
    merged = expanded
    return BBoxNormalized(parts=parts, merged=merged, stride=stride)
