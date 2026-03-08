from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..marine.bait_intelligence import bait_zones
from ..marine.current_model import current_vectors
from ..marine.ocean_swell_model import swell_vectors
from .cloud_engine import cloud_tiles_from_grids
from .jetstream_engine import jetstream_nodes_from_grids
from .wind_field import build_global_grids


@dataclass
class Cache:
    payload: Dict[str, Any]
    at: float


class GFSService:
    def __init__(self) -> None:
        self._cache: Optional[Cache] = None
        self.ttl = 600

    def _compute(self) -> Dict[str, Any]:
        grids = build_global_grids(stride=4)
        return {
            'generated_at': int(time.time()),
            'source': 'NOAA GFS / WaveWatchIII / HYCOM (procedural fallback)',
            'cloud_tiles': cloud_tiles_from_grids(grids),
            'jetstream': jetstream_nodes_from_grids(grids),
            'swell': swell_vectors(grids, limit=1200),
            'currents': current_vectors(grids, limit=1200),
            'bait_zones': bait_zones(grids, limit=400),
        }

    def _payload(self) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache.at < self.ttl:
            return self._cache.payload
        payload = self._compute()
        self._cache = Cache(payload=payload, at=now)
        return payload

    def cloud_tiles(self) -> Dict[str, Any]:
        p = self._payload()
        return {'items': p['cloud_tiles'], 'generated_at': p['generated_at'], 'source': p['source']}

    def jetstream(self) -> Dict[str, Any]:
        p = self._payload()
        return {'items': p['jetstream'], 'generated_at': p['generated_at']}

    def swell(self) -> Dict[str, Any]:
        p = self._payload()
        return {'items': p['swell'], 'generated_at': p['generated_at']}

    def currents(self) -> Dict[str, Any]:
        p = self._payload()
        return {'items': p['currents'], 'generated_at': p['generated_at']}

    def bait(self) -> Dict[str, Any]:
        p = self._payload()
        return {'items': p['bait_zones'], 'generated_at': p['generated_at']}

    def forecast_cells(self, limit: int) -> List[Dict[str, Any]]:
        items = self.bait()['items'][:limit]
        cells = []
        for i in items:
            cells.append(
                {
                    'lat': i['lat'],
                    'lng': i['lng'],
                    'sst': 18 + i['density'] * 7,
                    'chlorophyll': i['density'],
                    'current_speed': i['velocity'],
                    'wind_speed': i['wind_speed'],
                    'moon_phase': 0.5,
                    'depth': i['depth'],
                    'distance_to_coast': abs(i['lat']) * 0.8,
                    'predator_signal': i['predator_probability'],
                    'report_density': min(1.0, i['bait_score'] / 100),
                }
            )
        return cells
