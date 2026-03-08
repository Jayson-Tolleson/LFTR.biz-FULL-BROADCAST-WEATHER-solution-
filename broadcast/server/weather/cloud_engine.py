from __future__ import annotations

from typing import Any, Dict, Iterable, List


def cloud_tiles_from_grids(data: Dict[str, Iterable[float]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for lat, lng, cover, precip, u, v in zip(
        data['lat'], data['lng'], data['cloud_cover'], data['precip'], data['wind_u'], data['wind_v']
    ):
        coverage = max(0.0, min(1.0, float(cover)))
        if coverage < 0.1:
            continue
        precip_rate = float(max(0.0, precip))
        base_alt = 1400 + coverage * 2100
        top_alt = base_alt + 2400 + coverage * 5400
        density = min(1.0, max(0.2, coverage * 0.8 + min(1.0, precip_rate / 30.0) * 0.35))
        storm_energy = min(1.0, 0.45 * density + 0.55 * min(1.0, precip_rate / 35.0))
        importance = min(1.0, 0.65 * coverage + 0.35 * min(1.0, precip_rate / 20.0))
        items.append(
            {
                'lat': round(float(lat), 3),
                'lng': round(float(lng), 3),
                'base_altitude_m': round(base_alt, 1),
                'top_altitude_m': round(top_alt, 1),
                'coverage': round(coverage, 3),
                'density': round(density, 3),
                'precip_rate': round(precip_rate, 2),
                'storm_energy': round(storm_energy, 3),
                'wind_u': round(float(u), 2),
                'wind_v': round(float(v), 2),
                'importance': round(importance, 3),
            }
        )
    items.sort(key=lambda x: x['importance'], reverse=True)
    return items
