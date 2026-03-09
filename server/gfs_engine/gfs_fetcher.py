from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests


@dataclass
class GFSFetchResult:
    ok: bool
    cycle: str
    forecast_hour: int
    path: Path | None
    error: str = ""


class GFSFetcher:
    """Lightweight async downloader for latest NOMADS subset payloads."""

    BASE_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

    def __init__(self, cache_dir: Path, timeout_s: float = 25.0) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s

    def _cycle_candidates(self) -> list[tuple[str, int]]:
        now = datetime.now(timezone.utc)
        cycle = (now.hour // 6) * 6
        out: list[tuple[str, int]] = []
        for i in range(4):
            c = (cycle - 6 * i) % 24
            d = now
            if cycle - 6 * i < 0:
                d = now.replace(day=max(1, now.day - 1))
            out.append((d.strftime("%Y%m%d"), c))
        return out

    def _build_url(self, day: str, cyc: int, fhr: int, vars_needed: Iterable[str]) -> str:
        file_name = f"gfs.t{cyc:02d}z.pgrb2.0p25.f{fhr:03d}"
        q = [
            ("dir", f"/gfs.{day}/{cyc:02d}/atmos"),
            ("file", file_name),
            ("subregion", ""),
            ("leftlon", "-180"),
            ("rightlon", "180"),
            ("toplat", "80"),
            ("bottomlat", "-80"),
        ]
        for v in vars_needed:
            q.append((f"var_{v}", "on"))
        return self.BASE_URL + "?" + "&".join(f"{k}={v}" for k, v in q)

    def _download_once(self, url: str, out_path: Path) -> None:
        r = requests.get(url, timeout=self.timeout_s)
        r.raise_for_status()
        out_path.write_bytes(r.content)

    async def fetch_latest(self, forecast_hour: int = 0) -> GFSFetchResult:
        vars_needed = ["TMP", "RH", "UGRD", "VGRD", "TCDC", "PRATE"]
        loop = asyncio.get_running_loop()
        for day, cyc in self._cycle_candidates():
            out_path = self.cache_dir / f"gfs_{day}_{cyc:02d}_f{forecast_hour:03d}.grib2"
            url = self._build_url(day, cyc, forecast_hour, vars_needed)
            try:
                await loop.run_in_executor(None, self._download_once, url, out_path)
                if out_path.exists() and out_path.stat().st_size > 4096:
                    return GFSFetchResult(True, f"{day}{cyc:02d}", forecast_hour, out_path)
            except Exception as exc:
                last_error = str(exc)
                continue
        return GFSFetchResult(False, "", forecast_hour, None, error=last_error if 'last_error' in locals() else "no cycle available")
