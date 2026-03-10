from __future__ import annotations

import math
from typing import Any


def tile_to_bounds(z: int, x: int, y: int) -> dict[str, float]:
    z = max(0, int(z))
    n = 2 ** z
    x = max(0, min(n - 1, int(x)))
    y = max(0, min(n - 1, int(y)))
    west = (x / n) * 360.0 - 180.0
    east = ((x + 1) / n) * 360.0 - 180.0
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return {"west": west, "south": south, "east": east, "north": north}


def bounds_to_tile_range(bounds: dict[str, float], z: int) -> dict[str, int]:
    z = max(0, int(z))
    n = 2 ** z
    west = float(bounds.get("west", -180))
    east = float(bounds.get("east", 180))
    south = max(-85.05112878, float(bounds.get("south", -85)))
    north = min(85.05112878, float(bounds.get("north", 85)))

    def lon_to_x(lon: float) -> int:
        return int((lon + 180.0) / 360.0 * n)

    def lat_to_y(lat: float) -> int:
        lat_rad = math.radians(lat)
        return int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)

    return {
        "x_min": max(0, min(n - 1, lon_to_x(west))),
        "x_max": max(0, min(n - 1, lon_to_x(east))),
        "y_min": max(0, min(n - 1, lat_to_y(north))),
        "y_max": max(0, min(n - 1, lat_to_y(south))),
    }


def marching_squares_precip(lat, lon, precip_rate, z: int) -> list[dict[str, Any]]:
    if precip_rate is None:
        return []
    thresholds = [0.1, 1.0, 4.0, 12.0]
    stride = 8 if z < 3 else 4 if z < 6 else 2
    out: list[dict[str, Any]] = []
    for t in thresholds:
        for y in range(0, precip_rate.shape[0] - 1, stride):
            for x in range(0, precip_rate.shape[1] - 1, stride):
                v = float(precip_rate[y, x])
                if v < t:
                    continue
                poly = [
                    [float(lon[y, x]), float(lat[y, x])],
                    [float(lon[y, x + 1]), float(lat[y, x + 1])],
                    [float(lon[y + 1, x + 1]), float(lat[y + 1, x + 1])],
                    [float(lon[y + 1, x]), float(lat[y + 1, x])],
                    [float(lon[y, x]), float(lat[y, x])],
                ]
                out.append({"intensity": t, "polygon": poly})
    return out
