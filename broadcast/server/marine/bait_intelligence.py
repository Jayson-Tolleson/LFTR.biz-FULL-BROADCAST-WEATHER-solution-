from __future__ import annotations

import math
from typing import Dict, Iterable, List

from .fish_school_model import build_school_markers, school_count_from_classification


def classify(score: float) -> str:
    if score < 30:
        return 'LOW'
    if score < 60:
        return 'MEDIUM'
    if score < 80:
        return 'HIGH'
    return 'EXTREME'


def bait_zones(data: Dict[str, Iterable[float]], limit: int = 400) -> List[dict]:
    zones: List[dict] = []
    for lat, lng, sst, chl, cu, cv, wind_u, wind_v in zip(
        data['lat'], data['lng'], data['sst'], data['chlorophyll'], data['current_u'], data['current_v'], data['wind_u'], data['wind_v']
    ):
        latf, lngf = float(lat), float(lng)
        if abs(latf) > 65:
            continue

        density = max(0.1, min(1.0, float(chl) / 1.2))
        velocity = (float(cu) ** 2 + float(cv) ** 2) ** 0.5
        direction = (math.degrees(math.atan2(float(cu), float(cv))) + 360) % 360
        depth = max(5, 120 - abs(latf) * 1.2)

        predator_signal = max(0.0, min(1.0, (float(chl) * 0.5) + (1 - abs(22 - float(sst)) / 22) * 0.5))
        temperature_factor = max(0.0, min(1.0, 1 - abs(21 - float(sst)) / 16))
        current_factor = max(0.0, min(1.0, 0.3 + velocity))
        light_factor = max(0.0, min(1.0, 0.5 + math.cos(math.radians(lngf)) * 0.25))

        bait_score = (
            density * 0.35 + predator_signal * 0.25 + temperature_factor * 0.15 + current_factor * 0.15 + light_factor * 0.10
        ) * 100

        boil_probability = min(1.0, predator_signal * 0.9)
        level = classify(bait_score)
        spheres = school_count_from_classification(level)

        zones.append(
            {
                'lat': round(latf, 3),
                'lng': round(lngf, 3),
                'density': round(density, 3),
                'velocity': round(velocity, 3),
                'direction': round(direction, 1),
                'depth': round(depth, 1),
                'predator_probability': round(predator_signal, 3),
                'boil_probability': round(boil_probability, 3),
                'bait_score': round(bait_score, 1),
                'classification': level,
                'wind_speed': round((float(wind_u) ** 2 + float(wind_v) ** 2) ** 0.5, 2),
                'spheres': build_school_markers(latf, lngf, spheres),
            }
        )

        if len(zones) >= limit:
            break
    zones.sort(key=lambda x: x['bait_score'], reverse=True)
    return zones
