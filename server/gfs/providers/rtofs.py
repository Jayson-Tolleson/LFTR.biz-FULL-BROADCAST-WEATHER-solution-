from __future__ import annotations

import asyncio
import json
import logging
import math
import urllib.request
from datetime import datetime
from typing import Any

from server.gfs.models import BBox
from server.gfs.providers.adapters import build_erddap_subset_request, build_station_enrichment_request, split_antimeridian, viewport_from_bbox
from server.gfs.providers.erddap_csv import ErddapParseDiagnostics, parse_erddap_grid
from server.gfs.serializers import iso_utc


log = logging.getLogger("server.gfs.provider.rtofs")

NOAA_TIDES_API = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
ERDDAP_OISST_CSV = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/ncdcOisst21Agg_LonPM180.csv"

COOPS_STATIONS = (
    ("9414290", 37.806, -122.465),
    ("8720218", 21.306, -157.867),
    ("8454000", 41.355, -71.968),
    ("8771013", 29.673, -93.836),
    ("9461380", 58.301, -134.419),
)


class RtofsProvider:
    """Ocean forcing provider fetching real ERDDAP viewport subsets.

    NOAA station currents are auxiliary enrichment only; no synthetic bbox grids.
    """

    def __init__(self) -> None:
        self._last_error: str | None = None
        self._last_fetch_at: datetime | None = None

    @staticmethod
    def _nearest_station(lat: float, lon: float) -> str:
        def dist2(item: tuple[str, float, float]) -> float:
            _, slat, slon = item
            return (lat - slat) ** 2 + (lon - slon) ** 2

        return min(COOPS_STATIONS, key=dist2)[0]

    @staticmethod
    def _http_json(url: str, timeout_s: float = 6.5) -> dict[str, Any] | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LFTR-GFS/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as res:
                return json.loads(res.read().decode("utf-8", errors="replace"))
        except Exception:
            return None

    @staticmethod
    def _http_text(url: str, timeout_s: float = 8.5) -> str | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LFTR-GFS/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    @staticmethod
    def _parse_erddap_grid(text: str | None) -> tuple[list[list[float]], ErddapParseDiagnostics]:
        return parse_erddap_grid(text, preferred_value_columns=("sst", "sea_surface_temperature"))

    @staticmethod
    def _merge_antimeridian_parts(parts: list[list[list[float]]]) -> list[list[float]]:
        grids = [g for g in parts if g]
        if not grids:
            return []
        if len(grids) == 1:
            return grids[0]

        min_rows = min(len(g) for g in grids)
        merged: list[list[float]] = []
        for i in range(min_rows):
            row: list[float] = []
            for g in grids:
                row.extend(g[i])
            merged.append(row)
        return merged

    def _fetch_station_currents(self, *, center_lat: float, center_lon: float) -> tuple[float | None, float | None]:
        station_id = self._nearest_station(center_lat, center_lon)
        url = (
            f"{NOAA_TIDES_API}?product=currents_predictions&application=lftr&station={station_id}"
            "&time_zone=gmt&units=english&interval=MAX_SLACK&format=json"
        )
        payload = self._http_json(url)
        arr = (payload or {}).get("current_predictions") or (payload or {}).get("cp") or []
        if not arr:
            return None, None
        p0 = arr[0]
        try:
            speed = float(p0.get("Velocity_Major") or p0.get("v") or p0.get("speed"))
            direction = float(p0.get("Direction_Bin") or p0.get("d") or p0.get("direction"))
        except Exception:
            return None, None
        rad = math.radians(direction)
        return speed * math.sin(rad), speed * math.cos(rad)

    @staticmethod
    def _constant_grid(ny: int, nx: int, value: float | None) -> list[list[float]]:
        if value is None or ny < 1 or nx < 1:
            return []
        return [[round(value, 4) for _ in range(nx)] for __ in range(ny)]

    def _fetch_subset_sync(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        viewport = viewport_from_bbox(bbox)
        slices = split_antimeridian(viewport)
        lon_convention = "pm180"
        sst_urls = build_erddap_subset_request(viewport, ERDDAP_OISST_CSV, ["sst"], stride, valid_time, lon_convention=lon_convention)
        raw_parts = [self._http_text(url) for url in sst_urls]
        parsed_parts = [self._parse_erddap_grid(body) for body in raw_parts]
        sst_parts = [grid for grid, _diag in parsed_parts]
        sst = self._merge_antimeridian_parts(sst_parts)
        diagnostics = [diag for _grid, diag in parsed_parts]

        if not sst:
            lon_convention = "0360"
            sst_urls = build_erddap_subset_request(viewport, ERDDAP_OISST_CSV, ["sst"], stride, valid_time, lon_convention=lon_convention)
            raw_parts = [self._http_text(url) for url in sst_urls]
            parsed_parts = [self._parse_erddap_grid(body) for body in raw_parts]
            sst_parts = [grid for grid, _diag in parsed_parts]
            diagnostics = [diag for _grid, diag in parsed_parts]
            sst = self._merge_antimeridian_parts(sst_parts)

        station_req = build_station_enrichment_request(viewport, valid_time)
        u, v = self._fetch_station_currents(center_lat=station_req["center_lat"], center_lon=station_req["center_lon"])

        ny = len(sst)
        nx = len(sst[0]) if ny else 0
        payload = {
            "sst": sst,
            "current_u": self._constant_grid(ny, nx, u),
            "current_v": self._constant_grid(ny, nx, v),
            "source_meta": {
                "ocean_source": "erddap_griddap",
                "station_source": "noaa_coops_aux",
                "station_enrichment": {"current_u": u, "current_v": v},
                "subset_urls": len(sst_urls),
                "lon_convention": lon_convention,
                "real_subset": bool(sst),
            },
        }
        self._last_fetch_at = datetime.utcnow()
        self._last_error = None
        log.info(
            "rtofs subset fetched bbox=%s viewport=%s erddap_slices=%s stride=%s sst_shape=%sx%s real_subset=%s",
            bbox.as_list(),
            {"west": viewport.west, "south": viewport.south, "east": viewport.east, "north": viewport.north},
            [{"lon_start": s.lon_start, "lon_stop": s.lon_stop} for s in slices],
            stride,
            ny,
            nx,
            bool(sst),
        )
        if not sst:
            diag_rows = [d.row_count for d in diagnostics]
            diag_lat = [d.lat_count for d in diagnostics]
            diag_lon = [d.lon_count for d in diagnostics]
            diag_rejected = [d.parser_rejected_rows for d in diagnostics]
            preview = [line for d in diagnostics for line in d.preview_lines[:2]][:4]
            log.warning(
                "rtofs subset empty bbox=%s dataset=%s vars=%s urls=%s rows=%s lat=%s lon=%s parser_rejected=%s http_success_no_data=%s lon_convention=%s preview=%s",
                bbox.as_list(),
                ERDDAP_OISST_CSV,
                ["sst"],
                sst_urls,
                diag_rows,
                diag_lat,
                diag_lon,
                diag_rejected,
                all(r is not None for r in raw_parts),
                lon_convention,
                preview,
            )
        return payload, valid_time

    async def fetch_subset(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        try:
            return await asyncio.to_thread(self._fetch_subset_sync, bbox=bbox, stride=stride, valid_time=valid_time)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("rtofs subset failed bbox=%s stride=%s err=%s", bbox.as_list(), stride, exc)
            return {
                "sst": [],
                "current_u": [],
                "current_v": [],
                "source_meta": {"ocean_source": "erddap_griddap", "station_source": "noaa_coops_aux", "real_subset": False, "error": str(exc)},
            }, valid_time

    def health(self) -> dict[str, Any]:
        return {
            "provider": "rtofs",
            "status": "viewport_subset_only",
            "upstreams": ["erddap_oisst", "noaa_coops_aux"],
            "last_fetch_at": iso_utc(self._last_fetch_at),
            "last_error": self._last_error,
        }
