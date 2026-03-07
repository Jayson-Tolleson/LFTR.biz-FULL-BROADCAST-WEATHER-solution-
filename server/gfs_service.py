from __future__ import annotations

import base64
import csv
import io
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from server.gfs_state import GFSState


_TRANSPARENT_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgB5o2a4AAAAASUVORK5CYII="
)


class GFSService:
    """Modular, namespaced GFS helpers for /gfs routes."""

    def __init__(self, static_dir: str) -> None:
        self.static_dir = Path(static_dir)
        self.data_dir = self.static_dir / "data"
        self.state = GFSState()

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _fish_csv_path(self) -> Path:
        return self.data_dir / "fishloclist.csv"

    def health(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "enabled": self.state.enabled,
            "source": self.state.source_name,
            "fish_count": len(self.state.fish_points),
            "last_refresh_ts": self.state.last_refresh_ts,
            "last_error": self.state.last_error,
            "ts": self._now_ms(),
        }

    def config(self) -> Dict[str, Any]:
        return {
            "enabled": self.state.enabled,
            "api_base": "/gfs/api",
            "ws_base": "/gfs/ws",
            "cache_ttl_seconds": self.state.cache_ttl_seconds,
            "ts": self._now_ms(),
        }

    def load_fish(self) -> Tuple[List[Dict[str, Any]], str | None]:
        csv_path = self._fish_csv_path()
        if not csv_path.exists():
            self.state.last_error = f"missing fish CSV: {csv_path}"
            return [], self.state.last_error

        points: List[Dict[str, Any]] = []
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for i, row in enumerate(reader):
                    if not row:
                        continue

                    lat_raw = row.get("lat") or row.get("latitude") or row.get("Lat") or row.get("Latitude")
                    lon_raw = row.get("lon") or row.get("lng") or row.get("longitude") or row.get("Lon") or row.get("Longitude")
                    if lat_raw is None or lon_raw is None:
                        continue

                    try:
                        lat = float(str(lat_raw).strip())
                        lon = float(str(lon_raw).strip())
                    except Exception:
                        continue

                    name = (row.get("name") or row.get("location") or row.get("label") or f"Location {i + 1}").strip()
                    points.append(
                        {
                            "id": (row.get("id") or row.get("locationId") or str(i + 1)).strip(),
                            "name": name,
                            "lat": lat,
                            "lon": lon,
                            "meta": {k: v for k, v in row.items() if k not in {"lat", "latitude", "Lat", "Latitude", "lon", "lng", "longitude", "Lon", "Longitude", "name", "location", "label", "id", "locationId"}},
                        }
                    )

            self.state.fish_points = points
            self.state.last_refresh_ts = self._now_ms()
            self.state.last_error = None
            return points, None
        except Exception as exc:  # noqa: BLE001
            self.state.last_error = f"failed to parse fish CSV: {exc}"
            return [], self.state.last_error

    def fish_payload(self) -> Dict[str, Any]:
        points, err = self.load_fish()
        return {
            "ok": err is None,
            "error": err,
            "count": len(points),
            "items": points,
            "ts": self._now_ms(),
        }

    def frame_payload(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "model": "GFS",
            "frame": {
                "wind_knots": 0,
                "wave_feet": 0,
                "cloud_pct": 0,
                "updated_at": self._now_ms(),
            },
        }

    def contours_payload(self) -> Dict[str, Any]:
        return {"ok": True, "features": [], "ts": self._now_ms()}

    def overlay_payload(self) -> Dict[str, Any]:
        return {"ok": True, "layers": [], "ts": self._now_ms()}

    def legend_payload(self) -> Dict[str, Any]:
        return {
            "ok": True,
            "legend": [
                {"name": "Low", "color": "#1f77b4"},
                {"name": "Medium", "color": "#ff7f0e"},
                {"name": "High", "color": "#d62728"},
            ],
            "ts": self._now_ms(),
        }

    def tile_png_bytes(self, z: int, x: int, y: int) -> bytes:
        _ = (z, x, y)
        return base64.b64decode(_TRANSPARENT_PNG_BASE64)

    async def ws_snapshot(self) -> Dict[str, Any]:
        return {
            "type": "gfs_update",
            "health": self.health(),
            "frame": self.frame_payload().get("frame", {}),
            "ts": self._now_ms(),
        }
