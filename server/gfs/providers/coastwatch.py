from __future__ import annotations

import asyncio
import csv
import io
import logging
import math
import urllib.request
from datetime import datetime

from server.gfs.models import BBox
from server.gfs.providers.adapters import build_erddap_subset_request, split_antimeridian, viewport_from_bbox


log = logging.getLogger("server.gfs.provider.coastwatch")

ERDDAP_CHL_CSV = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/erdMH1chlamday.csv"


class CoastwatchProvider:
    """CoastWatch bio provider fetching real viewport subsets only."""

    def __init__(self) -> None:
        self._last_error: str | None = None
        self._last_fetch_at: datetime | None = None

    @staticmethod
    def _http_text(url: str, timeout_s: float = 8.5) -> str | None:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LFTR-GFS/1.0"})
            with urllib.request.urlopen(req, timeout=timeout_s) as res:
                return res.read().decode("utf-8", errors="replace")
        except Exception:
            return None

    @staticmethod
    def _parse_erddap_grid(text: str | None) -> list[list[float]]:
        if not text:
            return []
        rows = [r for r in csv.reader(io.StringIO(text)) if r]
        if len(rows) < 2:
            return []

        values_by_lat: dict[float, list[float]] = {}
        for row in rows[1:]:
            if len(row) < 4:
                continue
            try:
                lat = float(row[1])
                val = float(row[-1])
            except Exception:
                continue
            if not math.isfinite(lat):
                continue
            values_by_lat.setdefault(lat, []).append(val if math.isfinite(val) else float("nan"))

        if not values_by_lat:
            return []

        lats = sorted(values_by_lat.keys())
        return [values_by_lat[lat] for lat in lats]

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

    @staticmethod
    def _water_color_grid(chlorophyll: list[list[float]]) -> list[list[float]]:
        out: list[list[float]] = []
        for row in chlorophyll:
            out_row: list[float] = []
            for ch in row:
                if not math.isfinite(ch) or ch <= 0:
                    out_row.append(float("nan"))
                else:
                    out_row.append(max(0.0, min(1.0, ch / 2.5)))
            out.append(out_row)
        return out

    def _fetch_subset_sync(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, object], datetime | None]:
        viewport = viewport_from_bbox(bbox)
        slices = split_antimeridian(viewport)
        urls = build_erddap_subset_request(viewport, ERDDAP_CHL_CSV, ["chlorophyll"], stride, valid_time)
        parts = [self._parse_erddap_grid(self._http_text(url)) for url in urls]
        chlorophyll = self._merge_antimeridian_parts(parts)
        payload = {
            "chlorophyll": chlorophyll,
            "water_color_index": self._water_color_grid(chlorophyll) if chlorophyll else [],
            "optional_ssh_anomaly": [],
            "source_meta": {
                "bio_source": "erddap_griddap",
                "subset_urls": len(urls),
                "real_subset": bool(chlorophyll),
            },
        }
        self._last_fetch_at = datetime.utcnow()
        self._last_error = None
        ny = len(chlorophyll)
        nx = len(chlorophyll[0]) if ny else 0
        log.info(
            "coastwatch subset fetched bbox=%s viewport=%s erddap_slices=%s stride=%s chlorophyll_shape=%sx%s real_subset=%s",
            bbox.as_list(),
            {"west": viewport.west, "south": viewport.south, "east": viewport.east, "north": viewport.north},
            [{"lon_start": s.lon_start, "lon_stop": s.lon_stop} for s in slices],
            stride,
            ny,
            nx,
            bool(chlorophyll),
        )
        return payload, valid_time

    async def fetch_subset(self, *, bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, object], datetime | None]:
        try:
            return await asyncio.to_thread(self._fetch_subset_sync, bbox=bbox, stride=stride, valid_time=valid_time)
        except Exception as exc:
            self._last_error = str(exc)
            log.warning("coastwatch subset failed bbox=%s stride=%s err=%s", bbox.as_list(), stride, exc)
            return {
                "chlorophyll": [],
                "water_color_index": [],
                "optional_ssh_anomaly": [],
                "source_meta": {"bio_source": "erddap_griddap", "real_subset": False, "error": str(exc)},
            }, valid_time

    def health(self) -> dict[str, object]:
        return {
            "provider": "coastwatch",
            "status": "viewport_subset_only",
            "upstreams": ["erddap_chlorophyll"],
            "last_fetch_at": self._last_fetch_at.isoformat() + "Z" if self._last_fetch_at else None,
            "last_error": self._last_error,
        }
