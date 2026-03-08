from __future__ import annotations

from typing import Dict, Iterable, List


def is_ocean(lat: float, lng: float) -> bool:
    return abs(lat) < 72 and not (-25 < lat < 55 and -30 < lng < 65)


def swell_vectors(data: Dict[str, Iterable[float]], limit: int = 1200) -> List[dict]:
    vectors: List[dict] = []
    for lat, lng, h, p, d in zip(data['lat'], data['lng'], data['wave_height'], data['wave_period'], data['wave_direction']):
        fl, fg = float(lat), float(lng)
        if not is_ocean(fl, fg):
            continue
        vectors.append(
            {
                'lat': round(fl, 3),
                'lng': round(fg, 3),
                'wave_height': round(float(h), 2),
                'wave_period': round(float(p), 2),
                'wave_direction': round(float(d), 1),
            }
        )
        if len(vectors) >= limit:
            break
    return vectors
