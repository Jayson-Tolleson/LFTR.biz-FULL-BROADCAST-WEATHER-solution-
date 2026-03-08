from __future__ import annotations

from typing import Any, Dict, Iterable, List


def normalize_importance(coverage: float, precip_rate: float) -> float:
    value = (coverage * 0.8) + (min(precip_rate, 20.0) / 20.0 * 0.2)
    return round(max(0.0, min(1.0, value)), 3)


def tiles_from_grids(
    lats: Iterable[float],
    lngs: Iterable[float],
    cloud_cover: Iterable[float],
    precip_rate: Iterable[float],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    for lat, lng, coverage, precip in zip(lats, lngs, cloud_cover, precip_rate):
        clamped_coverage = max(0.0, min(1.0, float(coverage)))
        if clamped_coverage < 0.1:
            continue

        base_alt = 1200 + clamped_coverage * 1800
        top_alt = base_alt + 1800 + (clamped_coverage * 3000)
        items.append(
            {
                "lat": round(float(lat), 3),
                "lng": round(float(lng), 3),
                "coverage": round(clamped_coverage, 3),
                "base_altitude_m": round(base_alt, 1),
                "top_altitude_m": round(top_alt, 1),
                "importance": normalize_importance(clamped_coverage, float(precip)),
            }
        )

    items.sort(key=lambda item: item["importance"], reverse=True)
    return items
