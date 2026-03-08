from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class GFSCache:
    payload: Dict[str, Any]
    generated_at: float


class GFSService:
    """Procedural weather/ocean provider with compact payloads."""

    def __init__(self) -> None:
        self._cache: Optional[GFSCache] = None
        self.cache_ttl_seconds = 600

    def _build_grids(self) -> Dict[str, np.ndarray]:
        stride = 4
        lats = np.arange(-60, 61, stride, dtype=float)
        lngs = np.arange(-180, 181, stride, dtype=float)

        lat_grid, lng_grid = np.meshgrid(lats, lngs, indexing="ij")

        cloud_cover = (np.sin(np.radians(lat_grid * 1.4)) + np.cos(np.radians(lng_grid * 0.8)) + 2) / 4
        precip_rate = np.maximum(0.0, (cloud_cover - 0.45) * 26.0)

        # Mid/upper-level winds for cloud advection
        u_wind = np.cos(np.radians(lat_grid * 0.8)) * 24
        v_wind = np.sin(np.radians(lng_grid * 0.9)) * 18

        # Jet-level winds (~250 mb)
        jet_u = 42 + (np.cos(np.radians(lat_grid * 2.2)) * 28) + (np.sin(np.radians(lng_grid * 0.65)) * 12)
        jet_v = (np.sin(np.radians(lat_grid * 1.7)) * 14) - (np.cos(np.radians(lng_grid * 0.9)) * 8)

        # Swell model synthesis
        wave_height = np.maximum(0.2, (np.sin(np.radians(lat_grid * 0.7)) + np.cos(np.radians(lng_grid * 0.6)) + 2.0) * 1.35)
        wave_period = 6.0 + wave_height * 2.9
        wave_direction = (np.degrees(np.arctan2(jet_u, jet_v)) + 360) % 360

        return {
            "lat": lat_grid.flatten(),
            "lng": lng_grid.flatten(),
            "cloud_cover": cloud_cover.flatten(),
            "precip_rate": precip_rate.flatten(),
            "u_wind": u_wind.flatten(),
            "v_wind": v_wind.flatten(),
            "jet_u": jet_u.flatten(),
            "jet_v": jet_v.flatten(),
            "wave_height": wave_height.flatten(),
            "wave_period": wave_period.flatten(),
            "wave_direction": wave_direction.flatten(),
        }

    def _generate_payload(self) -> Dict[str, Any]:
        grids = self._build_grids()

        items: List[Dict[str, Any]] = []
        rain: List[Dict[str, Any]] = []
        balloons: List[Dict[str, Any]] = []
        jetstream: List[Dict[str, Any]] = []
        swell: List[Dict[str, Any]] = []

        for i, (lat, lng, coverage, precip, u, v, ju, jv, wh, wp, wd) in enumerate(
            zip(
                grids["lat"],
                grids["lng"],
                grids["cloud_cover"],
                grids["precip_rate"],
                grids["u_wind"],
                grids["v_wind"],
                grids["jet_u"],
                grids["jet_v"],
                grids["wave_height"],
                grids["wave_period"],
                grids["wave_direction"],
            )
        ):
            coverage = float(max(0.0, min(1.0, coverage)))
            precip = float(max(0.0, precip))
            u = float(u)
            v = float(v)
            ju = float(ju)
            jv = float(jv)
            jet_speed = float((ju**2 + jv**2) ** 0.5)

            if coverage >= 0.10:
                base_alt = 1400 + coverage * 2200
                top_alt = base_alt + 2200 + coverage * 5200
                density = min(1.0, max(0.2, coverage * 0.82 + min(1.0, precip / 30.0) * 0.35))
                importance = min(1.0, 0.65 * coverage + 0.35 * min(1.0, precip / 24.0))
                storm_energy = min(1.0, 0.5 * density + 0.5 * min(1.0, precip / 35.0))

                items.append(
                    {
                        "lat": round(float(lat), 3),
                        "lng": round(float(lng), 3),
                        "base_altitude_m": round(base_alt, 1),
                        "top_altitude_m": round(top_alt, 1),
                        "coverage": round(coverage, 3),
                        "density": round(density, 3),
                        "precip_rate": round(precip, 2),
                        "storm_energy": round(storm_energy, 3),
                        "wind_u": round(u, 2),
                        "wind_v": round(v, 2),
                        "importance": round(importance, 3),
                    }
                )

            if precip > 0.2 and len(rain) < 450:
                rain.append(
                    {
                        "lat": round(float(lat), 3),
                        "lng": round(float(lng), 3),
                        "rate": round(precip, 2),
                        "u": round(u, 2),
                        "v": round(v, 2),
                    }
                )

            if i % 3 == 0 and len(balloons) < 120:
                speed = (u**2 + v**2) ** 0.5
                balloons.append(
                    {
                        "lat": round(float(lat), 3),
                        "lng": round(float(lng), 3),
                        "u": round(u, 2),
                        "v": round(v, 2),
                        "speed": round(speed, 2),
                        "altitude_ft": 10000,
                    }
                )

            if jet_speed >= 30 and len(jetstream) < 420:
                jet_altitude = 9000 + (np.cos(np.radians(float(lat))) * 1500)
                jetstream.append(
                    {
                        "lat": round(float(lat), 3),
                        "lng": round(float(lng), 3),
                        "altitude": round(float(jet_altitude), 1),
                        "wind_u": round(ju, 2),
                        "wind_v": round(jv, 2),
                        "speed": round(jet_speed, 2),
                    }
                )

            # ocean mask approximation
            over_ocean = abs(float(lat)) < 70 and not (-25 < float(lat) < 55 and -30 < float(lng) < 60)
            if over_ocean and len(swell) < 900:
                swell.append(
                    {
                        "lat": round(float(lat), 3),
                        "lng": round(float(lng), 3),
                        "wave_height": round(float(wh), 2),
                        "wave_period": round(float(wp), 2),
                        "wave_direction": round(float(wd), 1),
                    }
                )

        items.sort(key=lambda item: item["importance"], reverse=True)
        jetstream.sort(key=lambda item: item["speed"], reverse=True)

        return {
            "source": "NOAA GFS + WaveWatchIII (procedural fallback)",
            "generated_at": int(time.time()),
            "items": items,
            "rain": rain,
            "balloons": balloons,
            "jetstream": jetstream,
            "swell": swell,
        }

    def _payload(self) -> Dict[str, Any]:
        now = time.time()
        if self._cache and now - self._cache.generated_at < self.cache_ttl_seconds:
            return self._cache.payload

        payload = self._generate_payload()
        self._cache = GFSCache(payload=payload, generated_at=now)
        return payload

    def cloud_tiles_payload(self) -> Dict[str, Any]:
        payload = self._payload()
        return {"items": payload["items"], "generated_at": payload["generated_at"], "source": payload["source"]}

    def rain_payload(self) -> Dict[str, Any]:
        payload = self._payload()
        return {"items": payload["rain"], "generated_at": payload["generated_at"]}

    def balloons_payload(self) -> Dict[str, Any]:
        payload = self._payload()
        return {"items": payload["balloons"], "generated_at": payload["generated_at"]}

    def jetstream_payload(self) -> Dict[str, Any]:
        payload = self._payload()
        return {"items": payload["jetstream"], "generated_at": payload["generated_at"]}

    def swell_payload(self) -> Dict[str, Any]:
        payload = self._payload()
        return {"items": payload["swell"], "generated_at": payload["generated_at"]}
