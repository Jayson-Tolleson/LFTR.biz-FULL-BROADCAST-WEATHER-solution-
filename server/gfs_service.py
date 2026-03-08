from __future__ import annotations

import base64
import csv
import hashlib
import json
import math
import random
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
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

DEFAULT_HTTP_TIMEOUT = 6.5
ENV_CACHE_TTL_SECONDS = 900
NWS_CACHE_TTL_SECONDS = 1800
TIDE_CACHE_TTL_SECONDS = 1200
SST_CACHE_TTL_SECONDS = 10_800
NWS_API_BASE = "https://api.weather.gov"
NOAA_TIDES_API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
NOAA_COOPS_MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
DEFAULT_UA = "LFTR-GFS/1.0 (+https://lftr.biz)"
DEFAULT_WORLD_ENV_MARKER = {"lat": 34.2, "lon": -120.0}


CLOUD_REGIMES = {
    "marine_stratocumulus": {
        "deck_bias": 0.92,
        "tower_bias": 0.10,
        "wispy_bias": 0.08,
        "base_altitude_m": 700.0,
        "depth_m": 1400.0,
        "lateral_scale_km": 160.0,
        "underside_darkness": 0.22,
        "fringe_softness": 0.82,
    },
    "cumulus_field": {
        "deck_bias": 0.38,
        "tower_bias": 0.42,
        "wispy_bias": 0.12,
        "base_altitude_m": 1100.0,
        "depth_m": 2200.0,
        "lateral_scale_km": 95.0,
        "underside_darkness": 0.28,
        "fringe_softness": 0.58,
    },
    "frontal_shield": {
        "deck_bias": 0.78,
        "tower_bias": 0.24,
        "wispy_bias": 0.26,
        "base_altitude_m": 1200.0,
        "depth_m": 4200.0,
        "lateral_scale_km": 210.0,
        "underside_darkness": 0.34,
        "fringe_softness": 0.70,
    },
    "deep_convection": {
        "deck_bias": 0.18,
        "tower_bias": 0.96,
        "wispy_bias": 0.10,
        "base_altitude_m": 900.0,
        "depth_m": 9200.0,
        "lateral_scale_km": 120.0,
        "underside_darkness": 0.58,
        "fringe_softness": 0.36,
    },
    "cirrus_sheet": {
        "deck_bias": 0.20,
        "tower_bias": 0.06,
        "wispy_bias": 0.94,
        "base_altitude_m": 7600.0,
        "depth_m": 2400.0,
        "lateral_scale_km": 240.0,
        "underside_darkness": 0.14,
        "fringe_softness": 0.90,
    },
}


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


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
        self.http = requests.Session()
        self.http.headers.update({"User-Agent": DEFAULT_UA, "Accept": "application/json"})
        self.env_cache: Dict[str, Dict[str, Any]] = {}
        self.station_cache: Dict[str, Dict[str, Any]] = {}
        self.point_forecast_cache: Dict[str, Dict[str, Any]] = {}

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

    def _cache_get(self, cache: Dict[str, Dict[str, Any]], key: str, ttl_seconds: int) -> Any:
        row = cache.get(key)
        if not row:
            return None
        if (self._utc_now() - row["ts"]).total_seconds() > ttl_seconds:
            cache.pop(key, None)
            return None
        return row.get("value")

    def _cache_put(self, cache: Dict[str, Dict[str, Any]], key: str, value: Any) -> None:
        cache[key] = {"ts": self._utc_now(), "value": value}

    def _rounded_env_key(self, lat: float, lon: float, precision: int = 2) -> str:
        return f"{round(float(lat), precision)}:{round(float(lon), precision)}"

    def _http_json(self, url: str, params: Dict[str, Any] | None = None, timeout: float = DEFAULT_HTTP_TIMEOUT) -> Any:
        resp = self.http.get(url, params=params or {}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()

    def _safe_http_json(self, url: str, params: Dict[str, Any] | None = None, timeout: float = DEFAULT_HTTP_TIMEOUT) -> Any:
        try:
            return self._http_json(url, params=params, timeout=timeout)
        except Exception:
            return None

    def _utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _iso_utc(self, dt: datetime) -> str:
        return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def _time_of_day_bucket(self, lat: float, lon: float, ts_ms: int) -> str:
        _ = lat
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) + timedelta(hours=round(lon / 15.0))
        hour = dt.hour
        if 4 <= hour < 7:
            return "dawn"
        if 7 <= hour < 17:
            return "day"
        if 17 <= hour < 20:
            return "dusk"
        return "night"

    def _sun_angle_proxy(self, lat: float, lon: float, ts_ms: int) -> float:
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        day_frac = (dt.hour + dt.minute / 60.0 + lon / 15.0) / 24.0
        seasonal = math.cos(2 * math.pi * ((dt.timetuple().tm_yday - 172) / 365.25))
        solar = math.sin(2 * math.pi * (day_frac - 0.25))
        lat_factor = math.cos(math.radians(lat))
        return _clamp((solar * lat_factor * 0.85 + seasonal * 0.15 + 1.0) / 2.0, 0.0, 1.0)

    def _moon_phase_proxy(self, ts_ms: int) -> Dict[str, Any]:
        days = ts_ms / 1000.0 / 86400.0
        synodic = 29.53058867
        phase = (days % synodic) / synodic
        illum = 0.5 * (1 - math.cos(2 * math.pi * phase))
        if phase < 0.03 or phase > 0.97:
            label = "new"
        elif 0.47 <= phase <= 0.53:
            label = "full"
        elif phase < 0.5:
            label = "waxing"
        else:
            label = "waning"
        return {"phase": round(phase, 4), "illumination": round(illum, 4), "label": label}

    BAIT_TERMS = ["anchovy", "sardine", "mackerel", "smelt", "herring", "bait ball", "boil", "chum", "squid", "shiner"]
    RIG_TERMS = ["carolina", "sabiki", "flyline", "float", "jig", "swimbait", "dropper loop", "texas rig", "spinner", "topwater"]
    SPECIES_TERMS = ["halibut", "calico", "bass", "yellowtail", "perch", "corbina", "tuna", "snapper", "tarpon", "snook"]

    def _extract_text_blobs(self, point: Dict[str, Any]) -> List[str]:
        blobs: List[str] = []
        for field in ("name", "location_key", "description", "notes", "intent", "area", "zone"):
            v = point.get(field)
            if isinstance(v, str) and v.strip():
                blobs.append(v.strip())

        meta = point.get("meta") if isinstance(point.get("meta"), dict) else {}
        for k, v in meta.items():
            if isinstance(v, str) and v.strip() and any(tok in k.lower() for tok in ("report", "note", "comment", "desc", "bait", "rig", "species")):
                blobs.append(v.strip())

        store = self._load_store()
        key = self._normalize_location_key(str(point.get("location_key") or ""))
        rec = ((store.get("locations") or {}).get(key) or {}) if key else {}
        report_text = rec.get("report_text")
        if isinstance(report_text, str) and report_text.strip():
            blobs.append(report_text.strip())
        return blobs

    def _term_counts(self, texts: List[str], terms: List[str]) -> Dict[str, int]:
        joined = "\n".join(texts).lower()
        out: Dict[str, int] = {}
        for t in terms:
            cnt = len(re.findall(rf"\b{re.escape(t.lower())}\b", joined))
            if cnt:
                out[t] = cnt
        return out

    def _history_features_for_point(self, point: Dict[str, Any]) -> Dict[str, Any]:
        texts = self._extract_text_blobs(point)
        bait_mentions = self._term_counts(texts, self.BAIT_TERMS)
        rig_mentions = self._term_counts(texts, self.RIG_TERMS)
        species_mentions = self._term_counts(texts, self.SPECIES_TERMS)
        report_count = len([t for t in texts if len(t) > 12])

        richness = _clamp((report_count / 8.0) + (sum(bait_mentions.values()) / 12.0), 0.0, 1.0)
        success = _clamp(0.25 + richness * 0.55 + (sum(species_mentions.values()) / 15.0), 0.0, 1.0)

        def top_terms(d: Dict[str, int]) -> List[str]:
            return [k for k, _ in sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:3]]

        return {
            "report_count": report_count,
            "bait_mentions": bait_mentions,
            "rig_mentions": rig_mentions,
            "species_mentions": species_mentions,
            "historical_success_score": round(success, 4),
            "dominant_baits": top_terms(bait_mentions),
            "dominant_rigs": top_terms(rig_mentions),
            "dominant_species": top_terms(species_mentions),
        }

    def _supports_us_station_enrichment(self, lat: float, lon: float) -> bool:
        return (15.0 <= lat <= 72.5 and -170.0 <= lon <= -60.0)

    def _supports_global_gfs(self, lat: float, lon: float) -> bool:
        return -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0

    def _point_has_report_history(self, point: Dict[str, Any]) -> bool:
        h = self._history_features_for_point(point)
        return h.get("report_count", 0) > 0 or bool(h.get("dominant_baits"))

    def _select_environment_strategy(self, lat: float, lon: float, point: Dict[str, Any]) -> str:
        if self._supports_us_station_enrichment(lat, lon):
            return "station_enriched_us"
        if self._supports_global_gfs(lat, lon):
            return "global_model_gfs"
        if self._point_has_report_history(point):
            return "history_augmented"
        return "heuristic_only"

    def _candidate_coops_stations(self, lat: float, lon: float) -> List[Dict[str, Any]]:
        _ = (lat, lon)
        return [
            {"id": "9410170", "name": "San Diego", "lat": 32.714, "lon": -117.173},
            {"id": "9414290", "name": "San Francisco", "lat": 37.806, "lon": -122.465},
            {"id": "8724580", "name": "Key West", "lat": 24.556, "lon": -81.807},
            {"id": "8518750", "name": "The Battery", "lat": 40.701, "lon": -74.014},
            {"id": "9455920", "name": "Anchorage", "lat": 61.238, "lon": -149.89},
        ]

    def _haversine_km(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        r = 6371.0
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * r * math.asin(math.sqrt(max(0.0, min(1.0, a))))

    def _nearest_coops_station_id(self, lat: float, lon: float) -> str | None:
        stations = self._candidate_coops_stations(lat, lon)
        nearest = None
        best = 1e9
        for st in stations:
            d = self._haversine_km(lat, lon, float(st["lat"]), float(st["lon"]))
            if d < best:
                best = d
                nearest = st["id"]
        return nearest if best <= 900 else None

    def _fetch_nws_point_meta(self, lat: float, lon: float) -> Any:
        key = f"nws-point:{self._rounded_env_key(lat, lon, 2)}"
        cached = self._cache_get(self.point_forecast_cache, key, NWS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(f"{NWS_API_BASE}/points/{lat:.4f},{lon:.4f}")
        self._cache_put(self.point_forecast_cache, key, payload)
        return payload

    def _fetch_nws_hourly_forecast(self, lat: float, lon: float) -> Any:
        meta = self._fetch_nws_point_meta(lat, lon)
        hourly_url = (((meta or {}).get("properties") or {}).get("forecastHourly"))
        if not hourly_url:
            return None
        key = f"nws-hourly:{hourly_url}"
        cached = self._cache_get(self.point_forecast_cache, key, NWS_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(hourly_url)
        self._cache_put(self.point_forecast_cache, key, payload)
        return payload

    def _parse_nws_hourly_environment(self, payload: Any) -> Dict[str, Any]:
        periods = (((payload or {}).get("properties") or {}).get("periods") or [])
        p0 = periods[0] if periods else {}
        wind_speed_val = str(p0.get("windSpeed") or "0")
        m = re.search(r"(\d+)", wind_speed_val)
        wind_mph = float(m.group(1)) if m else 0.0
        return {
            "short_forecast": p0.get("shortForecast"),
            "temperature_f": p0.get("temperature"),
            "wind_speed_kt": round(wind_mph * 0.868976, 2),
            "wind_direction_text": p0.get("windDirection"),
            "precip_probability_pct": (((p0.get("probabilityOfPrecipitation") or {}).get("value")) or 0),
            "relative_humidity_pct": (((p0.get("relativeHumidity") or {}).get("value")) or 0),
            "is_daytime": bool(p0.get("isDaytime", True)),
        }

    def _fetch_coops_tides(self, station_id: str) -> Any:
        key = f"coops-tide:{station_id}"
        cached = self._cache_get(self.station_cache, key, TIDE_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        now = self._utc_now()
        payload = self._safe_http_json(
            NOAA_TIDES_API,
            params={
                "product": "predictions",
                "application": "lftr",
                "station": station_id,
                "datum": "MLLW",
                "time_zone": "gmt",
                "units": "english",
                "interval": "h",
                "format": "json",
                "begin_date": now.strftime("%Y%m%d"),
                "end_date": (now + timedelta(days=1)).strftime("%Y%m%d"),
            },
        )
        self._cache_put(self.station_cache, key, payload)
        return payload

    def _fetch_coops_currents(self, station_id: str) -> Any:
        key = f"coops-current:{station_id}"
        cached = self._cache_get(self.station_cache, key, TIDE_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = self._safe_http_json(
            NOAA_TIDES_API,
            params={
                "product": "currents_predictions",
                "application": "lftr",
                "station": station_id,
                "time_zone": "gmt",
                "units": "english",
                "interval": "MAX_SLACK",
                "format": "json",
            },
        )
        self._cache_put(self.station_cache, key, payload)
        return payload

    def _parse_tide_payload(self, payload: Any) -> Dict[str, Any]:
        preds = (payload or {}).get("predictions") or []
        if not preds:
            return {"tide_height_ft": None, "tide_time_utc": None}
        p0 = preds[0]
        height = p0.get("v")
        return {"tide_height_ft": float(height) if height is not None else None, "tide_time_utc": p0.get("t")}

    def _parse_currents_payload(self, payload: Any) -> Dict[str, Any]:
        arr = (payload or {}).get("current_predictions") or (payload or {}).get("cp") or []
        if not arr:
            return {"current_speed_kt": None, "current_direction_deg": None}
        p0 = arr[0]
        speed = p0.get("Velocity_Major") or p0.get("v") or p0.get("speed")
        direction = p0.get("Direction_Bin") or p0.get("d") or p0.get("direction")
        return {
            "current_speed_kt": float(speed) if speed not in (None, "") else None,
            "current_direction_deg": float(direction) if direction not in (None, "") else None,
        }

    def _gfsish_environment_proxy(self, lat: float, lon: float, ts_ms: int) -> Dict[str, Any]:
        hour_bucket = ts_ms // 3_600_000
        low, mid, high, precip, convection = self._cloud_density_triplet(lat, lon, int(hour_bucket))
        lat_r = math.radians(lat)
        lon_r = math.radians(lon)
        t = hour_bucket / 5.0
        u = 6.0 + 8.5 * math.sin(lat_r * 0.8 - lon_r * 0.3 + t * 0.14)
        v = 2.6 * math.cos(lon_r * 0.85 + t * 0.09)
        speed_ms = math.hypot(u, v)
        wind_dir = (math.degrees(math.atan2(u, v)) + 360.0) % 360.0
        cloud_cover = _clamp(low * 0.52 + mid * 0.31 + high * 0.17, 0.0, 1.0)
        pressure = 1016.0 - precip * 14.0 - convection * 8.0 + (0.5 - cloud_cover) * 5.0
        return {
            "cloud_cover_pct": int(round(cloud_cover * 100)),
            "precipitation_factor": round(precip, 4),
            "convection_factor": round(convection, 4),
            "wind_speed_kt": round(speed_ms * 1.94384, 2),
            "wind_direction_deg": round(wind_dir, 1),
            "wind_gust_kt": round(speed_ms * 1.94384 * (1.2 + convection * 0.4), 2),
            "pressure_mb": round(pressure, 1),
        }

    def _fetch_sst_erddap(self, lat: float, lon: float) -> Dict[str, Any] | None:
        key = f"sst:{self._rounded_env_key(lat, lon, 1)}"
        cached = self._cache_get(self.env_cache, key, SST_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached
        payload = None
        self._cache_put(self.env_cache, key, payload)
        return payload

    def _build_station_plus_gfs_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        nws = self._parse_nws_hourly_environment(self._fetch_nws_hourly_forecast(lat, lon))
        station_id = self._nearest_coops_station_id(lat, lon)
        tides = self._parse_tide_payload(self._fetch_coops_tides(station_id)) if station_id else {}
        currents = self._parse_currents_payload(self._fetch_coops_currents(station_id)) if station_id else {}
        moon = self._moon_phase_proxy(ts_ms)
        return {
            **env,
            **{k: v for k, v in nws.items() if v is not None},
            **{k: v for k, v in tides.items() if v is not None},
            **{k: v for k, v in currents.items() if v is not None},
            "station_id": station_id,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": moon,
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_global_gfs_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        sst = self._fetch_sst_erddap(lat, lon) or {}
        return {
            **env,
            **sst,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_history_augmented_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        history = self._history_features_for_point(point)
        return {
            **env,
            "history_success_score": history.get("historical_success_score", 0),
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_heuristic_environment(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        _ = point
        env = self._gfsish_environment_proxy(lat, lon, ts_ms)
        return {
            **env,
            "sun_angle": round(self._sun_angle_proxy(lat, lon, ts_ms), 4),
            "moon": self._moon_phase_proxy(ts_ms),
            "time_bucket": self._time_of_day_bucket(lat, lon, ts_ms),
        }

    def _build_environment_meta(self, strategy: str, env: Dict[str, Any], point: Dict[str, Any], history: Dict[str, Any]) -> Dict[str, Any]:
        _ = (env, point)
        mod_map = {
            "station_enriched_us": 1.12,
            "global_model_gfs": 1.0,
            "history_augmented": 0.9,
            "heuristic_only": 0.78,
        }
        sources = ["solar_lunar", "history"]
        if strategy == "station_enriched_us":
            sources += ["global_gfs_proxy", "nws_hourly", "coops"]
        elif strategy == "global_model_gfs":
            sources += ["global_gfs_proxy", "sst_optional"]
        elif strategy == "history_augmented":
            sources += ["global_gfs_proxy", "report_history"]
        else:
            sources += ["global_gfs_proxy"]
        return {
            "source_tier": strategy,
            "sources_used": sources,
            "station_supported": strategy == "station_enriched_us",
            "global_supported": self._supports_global_gfs(float(point.get("lat") or 0), float(point.get("lon") or 0)),
            "history_supported": bool(history.get("report_count") or history.get("dominant_baits")),
            "coverage_mode": "worldwide_backbone",
            "confidence_modifier": mod_map.get(strategy, 0.78),
        }

    def _build_environment_context(self, lat: float, lon: float, ts_ms: int, point: Dict[str, Any]) -> Dict[str, Any]:
        cache_key = f"env:{self._rounded_env_key(lat, lon)}:{int(ts_ms // 600000)}:{hashlib.md5(str(point.get('location_key','')).encode()).hexdigest()[:8]}"
        cached = self._cache_get(self.env_cache, cache_key, ENV_CACHE_TTL_SECONDS)
        if cached is not None:
            return cached

        history = self._history_features_for_point(point)
        strategy = self._select_environment_strategy(lat, lon, point)
        if strategy == "station_enriched_us":
            environment = self._build_station_plus_gfs_environment(lat, lon, ts_ms, point)
        elif strategy == "global_model_gfs":
            environment = self._build_global_gfs_environment(lat, lon, ts_ms, point)
        elif strategy == "history_augmented":
            environment = self._build_history_augmented_environment(lat, lon, ts_ms, point)
        else:
            environment = self._build_heuristic_environment(lat, lon, ts_ms, point)
        env_meta = self._build_environment_meta(strategy, environment, point, history)
        out = {"environment": environment, "environment_meta": env_meta, "history": history}
        self._cache_put(self.env_cache, cache_key, out)
        return out

    def _score_bait_theory(self, lat: float, lon: float, ts_ms: int, env: Dict[str, Any], history: Dict[str, Any], env_meta: Dict[str, Any]) -> Dict[str, Any]:
        tod = self._time_of_day_bucket(lat, lon, ts_ms)
        tod_score = {"dawn": 0.84, "day": 0.58, "dusk": 0.82, "night": 0.48}.get(tod, 0.55)
        sun = float(env.get("sun_angle") or self._sun_angle_proxy(lat, lon, ts_ms))
        moon = ((env.get("moon") or {}).get("illumination") if isinstance(env.get("moon"), dict) else None)
        moon = float(moon or self._moon_phase_proxy(ts_ms).get("illumination") or 0.5)
        wind = float(env.get("wind_speed_kt") or 8.0)
        cloud = float(env.get("cloud_cover_pct") or 45.0) / 100.0
        precip = float(env.get("precipitation_factor") or 0.1)
        convection = float(env.get("convection_factor") or 0.1)
        current = float(env.get("current_speed_kt") or 0.7)
        tide_height = float(env.get("tide_height_ft") or 1.6)
        hist = float(history.get("historical_success_score") or 0.35)
        conf_mod = float(env_meta.get("confidence_modifier") or 0.8)

        wind_pref = 1.0 - min(1.0, abs(wind - 11.0) / 20.0)
        cloud_pref = 1.0 - abs(cloud - 0.45)
        precip_pen = max(0.0, 1.0 - precip * 0.55)
        conv_pen = max(0.0, 1.0 - convection * 0.35)
        current_pref = 1.0 - min(1.0, abs(current - 1.4) / 2.2)
        tide_pref = 1.0 - min(1.0, abs(tide_height - 2.2) / 4.5)

        presence = _clamp(
            (tod_score * 0.22 + wind_pref * 0.14 + cloud_pref * 0.12 + current_pref * 0.13 + tide_pref * 0.08 + sun * 0.09 + moon * 0.04 + hist * 0.18)
            * precip_pen
            * conv_pen,
            0.03,
            0.98,
        )

        confidence = int(round(_clamp((0.44 + hist * 0.24 + wind_pref * 0.14 + cloud_pref * 0.08) * conf_mod, 0.12, 0.97) * 100))
        if presence >= 0.7:
            intensity = "high"
        elif presence >= 0.42:
            intensity = "medium"
        else:
            intensity = "low"

        dominant_baits = history.get("dominant_baits") or []
        bait_candidates = dominant_baits[:]
        if not bait_candidates:
            bait_candidates = ["anchovy", "sardine", "mackerel"] if cloud < 0.65 else ["sardine", "squid", "smelt"]

        dominant_rigs = history.get("dominant_rigs") or []
        best_rig = dominant_rigs[0] if dominant_rigs else ("flyline" if presence > 0.6 else "dropper loop")

        if presence > 0.72:
            school_size = "large, cohesive bait balls"
            depth = [8, 38]
            agg = "tight_ball"
            mobility = "aggressive_migration"
            line_class = "20-30 lb"
        elif presence > 0.45:
            school_size = "medium scattered schools"
            depth = [18, 62]
            agg = "broken_patches"
            mobility = "moderate_roaming"
            line_class = "15-25 lb"
        else:
            school_size = "small fragmented schools"
            depth = [35, 95]
            agg = "loose_columns"
            mobility = "slow_drift"
            line_class = "10-20 lb"

        feeding_label = {"dawn": "Prime dawn feed", "dusk": "Prime dusk feed", "day": "Intermittent daytime feed", "night": "Low-light nighttime pick"}.get(tod, "Active window")

        surface_boils = _clamp(presence * (1.1 - cloud * 0.3) * (1 - precip * 0.35), 0.0, 1.0)
        predator_pressure = _clamp(0.32 + presence * 0.55 + hist * 0.2, 0.0, 1.0)

        return {
            "intensity": intensity,
            "confidence": confidence,
            "presence_probability": round(presence, 4),
            "school_size_estimate": school_size,
            "school_depth_band_ft": depth,
            "bait_type_candidates": bait_candidates,
            "aggregation_mode": agg,
            "mobility": mobility,
            "feeding_window": {"label": feeding_label, "confidence": round(_clamp(0.45 + presence * 0.5, 0.0, 1.0), 4)},
            "surface_boils_likelihood": round(surface_boils, 4),
            "predator_pressure": round(predator_pressure, 4),
            "school_summary": f"{school_size} expected in {depth[0]}-{depth[1]} ft with {bait_candidates[0]} emphasis.",
            "recommendation": {
                "best_bait": bait_candidates[0],
                "best_depth_zone_ft": depth,
                "best_rig": best_rig,
                "best_line_class": line_class,
            },
        }

    def _build_bait_intel(self, point: Dict[str, Any], ts_ms: int) -> Dict[str, Any]:
        lat = point.get("lat")
        lon = point.get("lon")
        if lat is None or lon is None:
            return self._heuristic_context(None, None, ts_ms)
        lat_f = float(lat)
        lon_f = float(lon)
        env_ctx = self._build_environment_context(lat_f, lon_f, ts_ms, point)
        bait = self._score_bait_theory(lat_f, lon_f, ts_ms, env_ctx["environment"], env_ctx["history"], env_ctx["environment_meta"])
        weather_summary = (
            f"Clouds {int(env_ctx['environment'].get('cloud_cover_pct', 0))}% | "
            f"Wind {env_ctx['environment'].get('wind_speed_kt', 0)} kt | "
            f"Pressure {env_ctx['environment'].get('pressure_mb', 'n/a')} mb"
        )
        water_context = (
            f"Current {env_ctx['environment'].get('current_speed_kt', 'n/a')} kt | "
            f"Tide {env_ctx['environment'].get('tide_height_ft', 'n/a')} ft | "
            f"Tier {env_ctx['environment_meta'].get('source_tier')}"
        )
        return {
            "bait": bait,
            "environment": env_ctx["environment"],
            "environment_meta": env_ctx["environment_meta"],
            "history": env_ctx["history"],
            "weather": {
                "summary": weather_summary,
                "water_context": water_context,
            },
        }

    def _heuristic_context(self, lat: float | None, lon: float | None, ts_ms: int) -> Dict[str, Any]:
        if lat is None or lon is None or not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
            fallback_point = {"lat": DEFAULT_WORLD_ENV_MARKER["lat"], "lon": DEFAULT_WORLD_ENV_MARKER["lon"], "location_key": "fallback", "meta": {}}
            intel = self._build_bait_intel(fallback_point, ts_ms)
            intel["bait"]["intensity"] = "low"
            intel["bait"]["confidence"] = min(intel["bait"].get("confidence", 42), 42)
            intel["environment_meta"]["source_tier"] = "heuristic_only"
            return intel

        point = {"lat": float(lat), "lon": float(lon), "location_key": f"pt-{self._rounded_env_key(float(lat), float(lon), 3)}", "meta": {}}
        return self._build_bait_intel(point, ts_ms)

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

    def _classify_cloud_regime(
        self,
        low: float,
        mid: float,
        high: float,
        precip: float,
        convection: float,
        lat: float,
    ) -> str:
        if convection > 0.72 and precip > 0.55:
            return "deep_convection"
        if high > 0.62 and low < 0.35 and precip < 0.35:
            return "cirrus_sheet"
        if low > 0.66 and mid < 0.46 and high < 0.34 and abs(lat) <= 42:
            return "marine_stratocumulus"
        if (low + mid + high) / 3.0 > 0.52 and mid > 0.48:
            return "frontal_shield"
        return "cumulus_field"

    def _derive_band_architecture(
        self,
        regime: str,
        band: str,
        density: float,
        precip: float,
        convection: float,
        u: float,
        v: float,
    ) -> Dict[str, Any]:
        speed = math.hypot(float(u or 0.0), float(v or 0.0))
        regime_cfg = CLOUD_REGIMES[regime]

        if band == "low":
            base_alt = regime_cfg["base_altitude_m"]
            thickness = _lerp(700.0, 2200.0, density)
            if regime == "marine_stratocumulus":
                thickness *= 0.85
            lateral_scale = _lerp(70.0, regime_cfg["lateral_scale_km"], density)
        elif band == "mid":
            base_alt = max(2200.0, regime_cfg["base_altitude_m"] + 1900.0)
            thickness = _lerp(1000.0, 3200.0, density)
            lateral_scale = _lerp(60.0, regime_cfg["lateral_scale_km"] * 0.78, density)
        else:
            base_alt = max(6500.0, regime_cfg["base_altitude_m"] + 6200.0)
            thickness = _lerp(900.0, 2600.0, density)
            lateral_scale = _lerp(90.0, regime_cfg["lateral_scale_km"] * 1.05, density)

        if regime == "deep_convection":
            if band == "low":
                thickness *= _lerp(1.2, 1.7, convection)
            elif band == "mid":
                thickness *= _lerp(1.3, 1.9, convection)
            else:
                thickness *= _lerp(1.1, 1.6, convection)

        if regime == "cirrus_sheet" and band == "high":
            lateral_scale *= 1.35
            thickness *= 0.72

        top_alt = base_alt + thickness
        coverage = _clamp(density * 0.92 + precip * 0.18, 0.0, 1.0)

        return {
            "density": round(density, 4),
            "base_altitude_m": round(base_alt, 1),
            "top_altitude_m": round(top_alt, 1),
            "thickness_m": round(thickness, 1),
            "coverage": round(coverage, 4),
            "lateral_scale_km": round(lateral_scale, 1),
            "wind": {
                "u": round(float(u or 0.0), 3),
                "v": round(float(v or 0.0), 3),
                "speed_ms": round(speed, 3),
            },
        }

    def _derive_tile_cloud_architecture(
        self,
        tile_id: str,
        lat_center: float,
        lon_center: float,
        low: float,
        mid: float,
        high: float,
        precip: float,
        convection: float,
        wind_low: Dict[str, float],
        wind_mid: Dict[str, float],
        wind_high: Dict[str, float],
        seed: int,
    ) -> Dict[str, Any]:
        regime = self._classify_cloud_regime(low, mid, high, precip, convection, lat_center)
        cfg = CLOUD_REGIMES[regime]

        coverage = _clamp(low * 0.42 + mid * 0.33 + high * 0.25 + precip * 0.12, 0.0, 1.0)
        opacity = _clamp(0.18 + coverage * 0.64 + convection * 0.10, 0.0, 1.0)

        u_low = float((wind_low or {}).get("u", 0.0))
        v_low = float((wind_low or {}).get("v", 0.0))
        u_mid = float((wind_mid or {}).get("u", 0.0))
        v_mid = float((wind_mid or {}).get("v", 0.0))
        u_high = float((wind_high or {}).get("u", 0.0))
        v_high = float((wind_high or {}).get("v", 0.0))

        shear = math.hypot(u_high - u_low, v_high - v_low)
        wind_shear = _clamp(shear / 35.0, 0.0, 1.0)

        bands = {
            "low": self._derive_band_architecture(regime, "low", low, precip, convection, u_low, v_low),
            "mid": self._derive_band_architecture(regime, "mid", mid, precip, convection, u_mid, v_mid),
            "high": self._derive_band_architecture(regime, "high", high, precip, convection, u_high, v_high),
        }

        base_altitude_m = min(bands["low"]["base_altitude_m"], bands["mid"]["base_altitude_m"], bands["high"]["base_altitude_m"])
        top_altitude_m = max(bands["low"]["top_altitude_m"], bands["mid"]["top_altitude_m"], bands["high"]["top_altitude_m"])
        vertical_depth_m = max(0.0, top_altitude_m - base_altitude_m)

        mean_u = (u_low + u_mid + u_high) / 3.0
        mean_v = (v_low + v_mid + v_high) / 3.0
        anvil_dir_deg = (math.degrees(math.atan2(mean_u, mean_v)) + 360.0) % 360.0
        anvil_spread_km = 0.0
        if regime == "deep_convection":
            anvil_spread_km = _lerp(35.0, 140.0, _clamp(convection * 0.75 + wind_shear * 0.25, 0.0, 1.0))
        elif regime in ("frontal_shield", "cirrus_sheet"):
            anvil_spread_km = _lerp(20.0, 90.0, _clamp(high * 0.7 + wind_shear * 0.3, 0.0, 1.0))

        organization = _clamp(0.25 + coverage * 0.35 + precip * 0.15 + convection * 0.25, 0.0, 1.0)
        underside_darkness = _clamp(cfg["underside_darkness"] + precip * 0.16 + convection * 0.14, 0.0, 1.0)
        fringe_softness = _clamp(cfg["fringe_softness"] - convection * 0.10 + high * 0.06, 0.0, 1.0)

        return {
            "regime": regime,
            "coverage": round(coverage, 4),
            "opacity": round(opacity, 4),
            "base_altitude_m": round(base_altitude_m, 1),
            "top_altitude_m": round(top_altitude_m, 1),
            "vertical_depth_m": round(vertical_depth_m, 1),
            "underside_darkness": round(underside_darkness, 4),
            "fringe_softness": round(fringe_softness, 4),
            "organization": round(organization, 4),
            "wind_shear": round(wind_shear, 4),
            "tower_bias": round(_clamp(cfg["tower_bias"] + convection * 0.18, 0.0, 1.0), 4),
            "deck_bias": round(_clamp(cfg["deck_bias"] + low * 0.08 - convection * 0.10, 0.0, 1.0), 4),
            "wispy_bias": round(_clamp(cfg["wispy_bias"] + high * 0.10, 0.0, 1.0), 4),
            "anvil_dir_deg": round(anvil_dir_deg, 2),
            "anvil_spread_km": round(anvil_spread_km, 1),
            "bands": bands,
        }

    def _build_cloud_subcells(self, seed: int, regime: str, coverage: float, convection: float, precip: float) -> List[Dict[str, Any]]:
        rng = random.Random(seed)
        base_count = 4
        if coverage > 0.5:
            base_count += 2
        if convection > 0.55:
            base_count += 2
        if regime == "deep_convection":
            base_count += 2

        subcells = []
        for idx in range(base_count):
            role = "fringe"
            if regime == "deep_convection" and idx == 0:
                role = "tower"
            elif idx < 2:
                role = "core"
            elif regime in ("marine_stratocumulus", "frontal_shield") and idx >= base_count - 2:
                role = "deck"
            elif regime == "cirrus_sheet":
                role = "wispy"

            subcells.append(
                {
                    "id": f"sc-{idx}",
                    "dx": round(rng.uniform(-0.38, 0.38), 4),
                    "dy": round(rng.uniform(-0.38, 0.38), 4),
                    "weight": round(_clamp(rng.uniform(0.45, 1.0) * (0.7 + coverage * 0.3), 0.0, 1.0), 4),
                    "role": role,
                }
            )
        return subcells

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

        importance = _clamp(
            low * 0.22
            + mid * 0.24
            + high * 0.18
            + precip * 0.18
            + convection * 0.18,
            0.0,
            1.0,
        )

        lat_idx = int((lat_min + 90) // 6)
        lon_idx = int((lon_min + 180) // 6)

        seed = int((abs(lat_center) * 1000 + abs(lon_center) * 100 + hour_bucket) % 10_000_000)
        wind = {
            "low": {"u": wind_low_u, "v": wind_low_v},
            "mid": {"u": wind_mid_u, "v": wind_mid_v},
            "high": {"u": wind_high_u, "v": wind_high_v},
        }
        arch = self._derive_tile_cloud_architecture(
            tile_id=f"gfs-{lat_idx:02d}-{lon_idx:02d}",
            lat_center=lat_center,
            lon_center=lon_center,
            low=low,
            mid=mid,
            high=high,
            precip=precip,
            convection=convection,
            wind_low=wind["low"],
            wind_mid=wind["mid"],
            wind_high=wind["high"],
            seed=seed,
        )

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
            "wind": wind,
            "seed": seed,
            "importance": round(importance, 4),
            "updated_at": self._now_ms(),
            **arch,
            "subcells": self._build_cloud_subcells(seed, arch["regime"], arch["coverage"], convection, precip),
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
            "note": "Cloud fields are heuristic but now include derived cloud-architecture metadata for volumetric-style front-end rendering.",
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
        now_ms = self._now_ms()
        if fish_match:
            intel = self._build_bait_intel(fish_match, now_ms)
        else:
            intel = self._heuristic_context(None, None, now_ms)
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
                    point = {
                        "id": (row.get("id") or row.get("locationId") or str(i + 1)).strip(),
                        "location_key": location_key,
                        "name": name,
                        "lat": lat,
                        "lon": lon,
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
                    point.update(self._build_bait_intel(point, self._now_ms()))
                    points.append(point)

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
        points, _ = self.load_fish()
        now_ms = self._now_ms()
        if points:
            seed_point = points[0]
        else:
            seed_point = {
                "location_key": "world-center",
                "lat": DEFAULT_WORLD_ENV_MARKER["lat"],
                "lon": DEFAULT_WORLD_ENV_MARKER["lon"],
                "meta": {},
            }
        intel = self._build_bait_intel(seed_point, now_ms)
        env = intel.get("environment", {})
        return {
            "ok": True,
            "model": "GFS",
            "frame": {
                "wind_knots": env.get("wind_speed_kt", 0),
                "wave_feet": env.get("wave_feet", 0),
                "cloud_pct": env.get("cloud_cover_pct", 0),
                "current_knots": env.get("current_speed_kt", 0),
                "sst_f": env.get("sst_f"),
                "updated_at": now_ms,
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
