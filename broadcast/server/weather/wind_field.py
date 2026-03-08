from __future__ import annotations

from typing import Dict

import numpy as np


def build_global_grids(stride: int = 4) -> Dict[str, np.ndarray]:
    lats = np.arange(-70, 71, stride, dtype=float)
    lngs = np.arange(-180, 181, stride, dtype=float)
    lat_grid, lng_grid = np.meshgrid(lats, lngs, indexing='ij')

    cloud_cover = (np.sin(np.radians(lat_grid * 1.3)) + np.cos(np.radians(lng_grid * 0.8)) + 2) / 4
    precip = np.maximum(0.0, (cloud_cover - 0.42) * 28.0)

    wind_u = np.cos(np.radians(lat_grid * 0.75)) * 20 + np.sin(np.radians(lng_grid * 0.45)) * 7
    wind_v = np.sin(np.radians(lng_grid * 0.9)) * 16

    jet_u = 45 + np.cos(np.radians(lat_grid * 2.2)) * 26 + np.sin(np.radians(lng_grid * 0.6)) * 14
    jet_v = np.sin(np.radians(lat_grid * 1.8)) * 14 - np.cos(np.radians(lng_grid * 0.7)) * 9

    current_u = np.sin(np.radians(lng_grid * 1.2)) * 0.7
    current_v = np.cos(np.radians(lat_grid * 1.1)) * 0.6

    sst = 28 - (np.abs(lat_grid) * 0.28) + np.sin(np.radians(lng_grid * 0.4)) * 1.2
    chlorophyll = 0.2 + (np.cos(np.radians(lat_grid * 2.4)) + 1) * 0.4

    wave_height = np.maximum(0.25, (np.sin(np.radians(lat_grid * 0.8)) + np.cos(np.radians(lng_grid * 0.6)) + 2.0) * 1.2)
    wave_period = 5.5 + wave_height * 3.0
    wave_direction = (np.degrees(np.arctan2(jet_u, jet_v)) + 360) % 360

    return {
        'lat': lat_grid.flatten(),
        'lng': lng_grid.flatten(),
        'cloud_cover': cloud_cover.flatten(),
        'precip': precip.flatten(),
        'wind_u': wind_u.flatten(),
        'wind_v': wind_v.flatten(),
        'jet_u': jet_u.flatten(),
        'jet_v': jet_v.flatten(),
        'current_u': current_u.flatten(),
        'current_v': current_v.flatten(),
        'sst': sst.flatten(),
        'chlorophyll': chlorophyll.flatten(),
        'wave_height': wave_height.flatten(),
        'wave_period': wave_period.flatten(),
        'wave_direction': wave_direction.flatten(),
    }
