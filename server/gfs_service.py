from __future__ import annotations

import base64
import csv
import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from werkzeug.utils import secure_filename

from server.gfs_state import GFSState


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = STATIC_DIR / "data"
FISH_CSV = DATA_DIR / "fishloclist.csv"


_TRANSPARENT_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/w8AAgMBgB5o2a4AAAAASUVORK5CYII="
)
_ALLOWED_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v"}


class GFSService:
    """Modular, namespaced GFS helpers for /gfs routes."""

    def __init__(self, static_dir: str) -> None:
        self.static_dir = Path(static_dir).resolve()
        self.data_dir = self.static_dir / "data"
        self.fishvid_dir = self.static_dir / "fishvid"
        self.store_path = self.data_dir / "gfs_location_store.json"
        self.state = GFSState()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.fishvid_dir.mkdir(parents=True, exist_ok=True)

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _fish_csv_path(self) -> Path:
        if self.static_dir == STATIC_DIR.resolve():
            return FISH_CSV
        return self.data_dir / "fishloclist.csv"

    def _normalize_location_key(self, value: str) -> str:
        raw = (value or "").strip().lower()
        raw = re.sub(r"[^a-z0-9_-]+", "-", raw)
        raw = re.sub(r"-{2,}", "-", raw).strip("-")
        return raw[:80]

    def _heuristic_context(self, lat: float | None, lon: float | None, ts_ms: int) -> Dict[str, Any]:
        minute = (ts_ms // 60000) % 60
        score = int((abs((lat or 0) * 1.7 + (lon or 0) * 0.9) + minute) % 100)
        if score >= 70:
            intensity = "high"
            window = "next 60-90 min"
        elif score >= 40:
            intensity = "medium"
            window = "next 2-3 hours"
        else:
            intensity = "low"
            window = "late window"

        cloud_pct = min(100, max(0, int((abs(lat or 0) * 3 + minute) % 100)))
        wind_knots = round(6 + (abs(lon or 0) % 14), 1)

        return {
            "bait": {
                "intensity": intensity,
                "confidence": score,
                "feeding_window": window,
                "school_summary": "Heuristic bait-school estimate from local marine/weather context",
            },
            "weather": {
                "summary": f"Clouds {cloud_pct}% | Wind {wind_knots} kt",
                "water_context": "Near-shore current and wind blend heuristic",
            },
        }

    def _stable_noise(self, a: float, b: float, c: float) -> float:
        mix = math.sin(a * 12.9898 + b * 78.233 + c * 37.719)
        return mix - math.floor(mix)

    def _cloud_density_triplet(self, lat: float, lon: float, hour_bucket: int) -> Tuple[float, float, float, float, float]:
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        t = hour_bucket / 6.0

        macro = (
            0.5
            + 0.25 * math.sin(2.4 * lat_r + 0.8 * lon_r + t * 0.9)
            + 0.18 * math.cos(3.7 * lon_r - t * 0.7)
            + 0.12 * math.sin(5.2 * (lat_r + lon_r) + t * 0.35)
        )
        macro = max(0.0, min(1.0, macro))

        frontness = abs(math.sin(lat_r * 2.7 + lon_r * 0.35 + t * 0.45))
        gradient = abs(math.cos(lat_r * 3.9 - lon_r * 1.4 + t * 0.28))
        convective_proxy = max(0.0, min(1.0, 0.35 + 0.55 * frontness * gradient))

        humidity_surface = max(0.0, min(1.0, 0.45 + 0.35 * math.cos(lat_r - t * 0.08) + 0.2 * self._stable_noise(lat, lon, t)))
        humidity_mid = max(0.0, min(1.0, 0.4 + 0.42 * math.sin(lat_r * 1.6 + t * 0.1) + 0.18 * self._stable_noise(lat * 0.7, lon * 0.5, t)))
        humidity_high = max(0.0, min(1.0, 0.35 + 0.45 * math.cos(lon_r * 0.9 + t * 0.09) + 0.15 * self._stable_noise(lat * 0.6, lon * 0.3, t + 2)))

        low = max(0.0, min(1.0, macro * 0.7 + humidity_surface * 0.35 + convective_proxy * 0.2 - 0.12))
        mid = max(0.0, min(1.0, macro * 0.6 + humidity_mid * 0.45 + convective_proxy * 0.24 - 0.16))
        high = max(0.0, min(1.0, macro * 0.55 + humidity_high * 0.5 + frontness * 0.22 - 0.18))

        precipitation_factor = max(0.0, min(1.0, (low * 0.5 + mid * 0.7 + convective_proxy * 0.65) - 0.38))
        convection_factor = max(0.0, min(1.0, convective_proxy * 0.85 + precipitation_factor * 0.3))

        return low, mid, high, precipitation_factor, convection_factor

    def _cloud_tile_payload(self, lat_min: float, lat_max: float, lon_min: float, lon_max: float, hour_bucket: int) -> Dict[str, Any]:
        lat_center = (lat_min + lat_max) / 2.0
        lon_center = (lon_min + lon_max) / 2.0

        low, mid, high, precip, convection = self._cloud_density_triplet(lat_center, lon_center, hour_bucket)

        lat_r = math.radians(lat_center)
        lon_r = math.radians(lon_center)
        t = hour_bucket / 5.0

        wind_low_u = round(3.5 + 7.5 * math.sin(lat_r + t * 0.15), 3)
        wind_low_v = round(2.0 * math.cos(lon_r - t * 0.12), 3)

        wind_mid_u = round(6.0 + 9.5 * math.sin(lat_r * 0.8 - lon_r * 0.3 + t * 0.14), 3)
        wind_mid_v = round(2.6 * math.cos(lon_r * 0.85 + t * 0.09), 3)

        wind_high_u = round(11.0 + 17.0 * math.sin(lat_r * 0.55 + lon_r * 0.22 - t * 0.16), 3)
        wind_high_v = round(3.8 * math.cos(lon_r * 0.6 - t * 0.11), 3)

        importance = max(low, mid, high) * 0.6 + precip * 0.25 + convection * 0.15

        lat_idx = int((lat_min + 90) // 6)
        lon_idx = int((lon_min + 180) // 6)

        return {
            "tile_id": f"gfs-{lat_idx:02d}-{lon_idx:02d}",
            "bounds": {
                "lat_min": round(lat_min, 4),
                "lat_max": round(lat_max, 4),
                "lon_min": round(lon_min, 4),
                "lon_max": round(lon_max, 4),
                "lat_center": round(lat_center, 4),
                "lon_center": round(lon_center, 4),
            },
            "low_density": round(low, 4),
            "mid_density": round(mid, 4),
            "high_density": round(high, 4),
            "precipitation_factor": round(precip, 4),
            "convection_factor": round(convection, 4),
            "altitude_low": 1200,
            "altitude_mid": 4200,
            "altitude_high": 9000,
            "wind": {
                "low": {"u": wind_low_u, "v": wind_low_v},
                "mid": {"u": wind_mid_u, "v": wind_mid_v},
                "high": {"u": wind_high_u, "v": wind_high_v},
            },
            "seed": int((abs(lat_center) * 1000 + abs(lon_center) * 100 + hour_bucket) % 10_000_000),
            "importance": round(min(1.0, importance), 4),
            "updated_at": self._now_ms(),
        }

    def cloud_tiles_payload(self) -> Dict[str, Any]:
        now_ms = self._now_ms()
        hour_bucket = now_ms // 3_600_000

        tiles: List[Dict[str, Any]] = []
        for lat in range(-90, 90, 6):
            lat_min = float(lat)
            lat_max = float(lat + 6)
            for lon in range(-180, 180, 6):
                lon_min = float(lon)
                lon_max = float(lon + 6)
                tile = self._cloud_tile_payload(lat_min, lat_max, lon_min, lon_max, hour_bucket)
                if max(tile["low_density"], tile["mid_density"], tile["high_density"]) < 0.08:
                    continue
                tiles.append(tile)

        tiles.sort(key=lambda t: t["importance"], reverse=True)
        summary = {
            "tile_count": len(tiles),
            "strong_tile_count": len([t for t in tiles if t["importance"] >= 0.72]),
            "storm_tile_count": len([t for t in tiles if t["precipitation_factor"] >= 0.55 or t["convection_factor"] >= 0.6]),
        }

        return {
            "ok": True,
            "source": "heuristic_forecast_from_latest_state",
            "heuristic": True,
            "note": "No direct grib/cloud cube in runtime; cloud fields are derived from deterministic macro/meso weather proxies.",
            "updated_at": now_ms,
            "items": tiles,
            "summary": summary,
            "bands": {
                "low": {
                    "altitude": 1200,
                    "description": "Lower deck cloud shell driven by low-level humidity and wind.",
                },
                "mid": {
                    "altitude": 4200,
                    "description": "Mid cloud masses preserving fronts and weather organization.",
                },
                "high": {
                    "altitude": 9000,
                    "description": "Upper cloud sheet/cirrus band driven by upper wind.",
                },
            },
        }

    def _load_store(self) -> Dict[str, Any]:
        if not self.store_path.exists():
            return {"locations": {}}
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:
            return {"locations": {}}

    def _save_store(self, data: Dict[str, Any]) -> None:
        self.store_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def _ensure_location_record(self, store: Dict[str, Any], location_key: str) -> Dict[str, Any]:
        locations = store.setdefault("locations", {})
        rec = locations.setdefault(
            location_key,
            {
                "location_key": location_key,
                "report_text": "",
                "report_updated_at": None,
                "live": {"active": False, "stream_url": "", "updated_at": None},
                "uploads": [],
            },
        )
        return rec

    def _location_media_payload(self, location_key: str) -> Dict[str, Any]:
        store = self._load_store()
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key", "location_key": "", "uploads": [], "live": {}, "report_text": ""}
        rec = self._ensure_location_record(store, key)
        fish_match = next((p for p in self.state.fish_points if p.get("location_key") == key), None)
        lat = fish_match.get("lat") if fish_match else None
        lon = fish_match.get("lon") if fish_match else None
        intel = self._heuristic_context(lat, lon, self._now_ms())
        return {
            "ok": True,
            "location_key": key,
            "label": fish_match.get("name") if fish_match else key,
            "report_text": rec.get("report_text") or "",
            "report_updated_at": rec.get("report_updated_at"),
            "uploads": rec.get("uploads") or [],
            "live": rec.get("live") or {"active": False, "stream_url": "", "updated_at": None},
            **intel,
            "ts": self._now_ms(),
        }

    def health(self) -> Dict[str, Any]:
        points, _ = self.load_fish()
        return {
            "ok": True,
            "enabled": self.state.enabled,
            "source": self.state.source_name,
            "fish_count": len(points),
            "last_refresh_ts": self.state.last_refresh_ts,
            "last_error": self.state.last_error,
            "fish_csv": str(self._fish_csv_path()),
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
                    location_key = self._normalize_location_key((row.get("location_key") or name or str(i + 1)))
                    if not location_key:
                        location_key = f"loc-{i+1}"

                    points.append(
                        {
                            "id": (row.get("id") or row.get("locationId") or str(i + 1)).strip(),
                            "location_key": location_key,
                            "name": name,
                            "lat": lat,
                            "lon": lon,
                            **self._heuristic_context(lat, lon, self._now_ms()),
                            "meta": {
                                k: v
                                for k, v in row.items()
                                if k
                                not in {
                                    "lat",
                                    "latitude",
                                    "Lat",
                                    "Latitude",
                                    "lon",
                                    "lng",
                                    "longitude",
                                    "Lon",
                                    "Longitude",
                                    "name",
                                    "location",
                                    "label",
                                    "id",
                                    "locationId",
                                    "location_key",
                                }
                            },
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
        return {"ok": err is None, "error": err, "count": len(points), "items": points, "ts": self._now_ms()}

    def frame_payload(self) -> Dict[str, Any]:
        clouds = self.cloud_tiles_payload()
        summary = clouds.get("summary", {})
        return {
            "ok": True,
            "model": "GFS",
            "frame": {
                "wind_knots": 0,
                "wave_feet": 0,
                "cloud_pct": min(100, int(summary.get("tile_count", 0) / 10)),
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

    def location_media(self, location_key: str) -> Dict[str, Any]:
        return self._location_media_payload(location_key)

    def upsert_report(self, location_key: str, report_text: str) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        rec["report_text"] = (report_text or "").strip()
        rec["report_updated_at"] = self._now_ms()
        self._save_store(store)
        return {"ok": True, **self._location_media_payload(key)}

    def upsert_live(self, location_key: str, active: bool, stream_url: str) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        rec["live"] = {
            "active": bool(active),
            "stream_url": (stream_url or "").strip(),
            "updated_at": self._now_ms(),
        }
        self._save_store(store)
        return {"ok": True, **self._location_media_payload(key)}

    def save_upload_video(self, location_key: str, filename: str, raw: bytes) -> Dict[str, Any]:
        key = self._normalize_location_key(location_key)
        if not key:
            return {"ok": False, "error": "missing location_key"}
        if not raw:
            return {"ok": False, "error": "empty upload"}

        safe_name = secure_filename(filename or "upload.mp4")
        ext = Path(safe_name).suffix.lower()
        if ext not in _ALLOWED_VIDEO_EXTS:
            return {"ok": False, "error": f"unsupported file type: {ext or 'none'}"}

        saved_name = f"{key}-{self._now_ms()}{ext}"
        out_path = self.fishvid_dir / saved_name
        out_path.write_bytes(raw)
        media_url = f"/static/fishvid/{saved_name}"

        store = self._load_store()
        rec = self._ensure_location_record(store, key)
        uploads = rec.setdefault("uploads", [])
        uploads.insert(
            0,
            {
                "url": media_url,
                "filename": saved_name,
                "mime": f"video/{ext.lstrip('.') if ext != '.mov' else 'quicktime'}",
                "uploaded_at": self._now_ms(),
            },
        )
        rec["uploads"] = uploads[:20]
        self._save_store(store)
        return {"ok": True, "location_key": key, "media_url": media_url, **self._location_media_payload(key)}

    async def ws_snapshot(self) -> Dict[str, Any]:
        cloud_payload = self.cloud_tiles_payload()
        return {
            "type": "gfs_update",
            "health": self.health(),
            "frame": self.frame_payload().get("frame", {}),
            "clouds": {
                "updated_at": cloud_payload.get("updated_at"),
                "summary": cloud_payload.get("summary", {}),
                "source": cloud_payload.get("source"),
            },
            "ts": self._now_ms(),
        }
