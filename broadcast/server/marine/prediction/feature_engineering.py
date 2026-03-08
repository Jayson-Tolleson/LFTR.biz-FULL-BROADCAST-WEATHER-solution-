from __future__ import annotations

import math
from typing import Dict


def build_feature_vector(cell: Dict) -> Dict:
    sst = float(cell.get('sst', 18.0))
    chlorophyll = float(cell.get('chlorophyll', 0.5))
    current_speed = float(cell.get('current_speed', 0.2))
    wind_speed = float(cell.get('wind_speed', 7.0))
    moon_phase = float(cell.get('moon_phase', 0.5))
    depth = float(cell.get('depth', 80.0))
    distance_to_coast = float(cell.get('distance_to_coast', 25.0))
    predator_signal = float(cell.get('predator_signal', 0.3))
    report_density = float(cell.get('report_density', 0.05))

    return {
        'sst': sst,
        'chlorophyll': chlorophyll,
        'current_speed': current_speed,
        'wind_speed': wind_speed,
        'moon_phase': moon_phase,
        'depth': depth,
        'distance_to_coast': distance_to_coast,
        'predator_signal': predator_signal,
        'report_density': report_density,
        'temp_optimality': max(0.0, 1 - abs(20 - sst) / 18),
        'coast_affinity': math.exp(-distance_to_coast / 55),
    }
