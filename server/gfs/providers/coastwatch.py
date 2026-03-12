from __future__ import annotations

import asyncio
import logging
import math
import urllib.request
from datetime import datetime

from server.gfs.models import BBox
from server.gfs.providers.adapters import build_erddap_subset_request, split_antimeridian, viewport_from_bbox
from server.gfs.providers.erddap_csv import ErddapParseDiagnostics, parse_erddap_grid


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
    def _parse_erddap_grid(text: str | None) -> tuple[list[list[float]], ErddapParseDiagnostics]:
        return parse_erddap_grid(text, preferred_value_columns=("chlorophyll", "chlor_a"))

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
        lon_convention = "pm180"
        urls = build_erddap_subset_request(viewport, ERDDAP_CHL_CSV, ["chlorophyll"], stride, valid_time, lon_convention=lon_convention)
        raw_parts = [self._http_text(url) for url in urls]
        parsed_parts = [self._parse_erddap_grid(body) for body in raw_parts]
        parts = [grid for grid, _diag in parsed_parts]
        chlorophyll = self._merge_antimeridian_parts(parts)
        diagnostics = [diag for _grid, diag in parsed_parts]

        if not chlorophyll:
            lon_convention = "0360"
            urls = build_erddap_subset_request(viewport, ERDDAP_CHL_CSV, ["chlorophyll"], stride, valid_time, lon_convention=lon_convention)
            raw_parts = [self._http_text(url) for url in urls]
            parsed_parts = [self._parse_erddap_grid(body) for body in raw_parts]
            parts = [grid for grid, _diag in parsed_parts]
            diagnostics = [diag for _grid, diag in parsed_parts]
            chlorophyll = self._merge_antimeridian_parts(parts)
        payload = {
            "chlorophyll": chlorophyll,
            "water_color_index": self._water_color_grid(chlorophyll) if chlorophyll else [],
            "optional_ssh_anomaly": [],
            "source_meta": {
                "bio_source": "erddap_griddap",
                "subset_urls": len(urls),
                "lon_convention": lon_convention,
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
        if not chlorophyll:
            diag_rows = [d.row_count for d in diagnostics]
            diag_lat = [d.lat_count for d in diagnostics]
            diag_lon = [d.lon_count for d in diagnostics]
            diag_rejected = [d.parser_rejected_rows for d in diagnostics]
            preview = [line for d in diagnostics for line in d.preview_lines[:2]][:4]
            log.warning(
                "coastwatch subset empty bbox=%s dataset=%s vars=%s urls=%s rows=%s lat=%s lon=%s parser_rejected=%s http_success_no_data=%s lon_convention=%s preview=%s",
                bbox.as_list(),
                ERDDAP_CHL_CSV,
                ["chlorophyll"],
                urls,
                diag_rows,
                diag_lat,
                diag_lon,
                diag_rejected,
                all(r is not None for r in raw_parts),
                lon_convention,
                preview,
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
