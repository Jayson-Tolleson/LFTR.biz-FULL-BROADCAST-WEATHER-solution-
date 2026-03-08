from __future__ import annotations

from typing import Dict, Iterable, List


def current_vectors(data: Dict[str, Iterable[float]], limit: int = 1000) -> List[dict]:
    items: List[dict] = []
    for lat, lng, cu, cv in zip(data['lat'], data['lng'], data['current_u'], data['current_v']):
        items.append({'lat': round(float(lat), 3), 'lng': round(float(lng), 3), 'u': round(float(cu), 3), 'v': round(float(cv), 3)})
        if len(items) >= limit:
            break
    return items
