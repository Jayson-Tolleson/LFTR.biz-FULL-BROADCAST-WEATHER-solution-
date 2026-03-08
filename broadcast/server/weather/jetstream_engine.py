from __future__ import annotations

from typing import Any, Dict, Iterable, List


def jetstream_nodes_from_grids(data: Dict[str, Iterable[float]]) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for lat, lng, ju, jv in zip(data['lat'], data['lng'], data['jet_u'], data['jet_v']):
        u = float(ju)
        v = float(jv)
        speed = (u**2 + v**2) ** 0.5
        if speed < 30:
            continue
        altitude = 9000 + (1 - abs(float(lat)) / 70) * 3000
        nodes.append(
            {
                'lat': round(float(lat), 3),
                'lng': round(float(lng), 3),
                'altitude': round(altitude, 1),
                'wind_u': round(u, 2),
                'wind_v': round(v, 2),
                'speed': round(speed, 2),
            }
        )
    nodes.sort(key=lambda n: n['speed'], reverse=True)
    return nodes
