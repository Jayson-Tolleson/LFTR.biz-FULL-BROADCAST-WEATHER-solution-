from __future__ import annotations

from typing import List


def school_count_from_classification(level: str) -> int:
    return {'LOW': 20, 'MEDIUM': 45, 'HIGH': 80, 'EXTREME': 120}.get(level, 20)


def build_school_markers(lat: float, lng: float, count: int) -> List[dict]:
    markers: List[dict] = []
    for i in range(count):
        dx = ((i % 10) - 5) * 0.004
        dy = ((i // 10) - 5) * 0.004
        markers.append({'lat': round(lat + dy, 5), 'lng': round(lng + dx, 5), 'altitude': 4})
    return markers
