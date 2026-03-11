from __future__ import annotations

import importlib.util
import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
import xarray as xr

from server.gfs.errors import ProviderUnavailableError
from server.gfs.models import BBox


log = logging.getLogger("server.gfs.provider.thredds")


class ThreddsGfsProvider:
    """THREDDS FMRC Best OPeNDAP provider with bounded subset retrieval."""

    def __init__(self, dataset_url: str) -> None:
        self.dataset_url = dataset_url
        self._dataset = None
        self._opened_at: datetime | None = None
        self._open_engine: str | None = None
        self._last_error: str | None = None
        self._last_fetch_at: datetime | None = None

    def _dependencies(self) -> dict[str, bool]:
        return {
            "netCDF4": importlib.util.find_spec("netCDF4") is not None,
            "pydap": importlib.util.find_spec("pydap") is not None,
        }

    def _open_dataset(self):
        if self._dataset is not None:
            return self._dataset

        deps = self._dependencies()
        log.info("opening thredds dataset url=%s deps=%s", self.dataset_url, deps)

        for engine in ("netcdf4", "pydap"):
            try:
                log.info("attempt open_dataset engine=%s url=%s", engine, self.dataset_url)
                self._dataset = xr.open_dataset(self.dataset_url, engine=engine)
                self._opened_at = datetime.now(timezone.utc)
                self._open_engine = engine
                self._last_error = None
                log.info("dataset open success engine=%s url=%s", engine, self.dataset_url)
                return self._dataset
            except Exception as exc:
                self._last_error = f"open_dataset engine={engine} failed: {exc}"
                log.warning("dataset open failed engine=%s url=%s err=%s", engine, self.dataset_url, exc, exc_info=True)

        raise ProviderUnavailableError(
            f"failed to open dataset via netcdf4/pydap: {self._last_error}",
            provider="thredds_gfs",
        )

    @staticmethod
    def _coord_name(ds, candidates: tuple[str, ...]) -> str | None:
        for name in candidates:
            if name in ds.coords:
                return name
        return None

    @staticmethod
    def _safe_dt(value: Any) -> datetime | None:
        try:
            arr = np.array(value).reshape(-1)
            if arr.size == 0:
                return None
            ts = arr[0].astype("datetime64[s]").astype(int)
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
        except Exception:
            return None

    async def fetch_subset(self, *, variables: tuple[str, ...], bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        ds = self._open_dataset()
        try:
            subset = ds[list(variables)]

            time_coord = self._coord_name(subset, ("time", "valid_time"))
            if valid_time is not None and time_coord:
                subset = subset.sel({time_coord: valid_time}, method="nearest")

            lat_coord = self._coord_name(subset, ("lat", "latitude"))
            if lat_coord:
                lat_values = subset.coords[lat_coord].values
                if lat_values.size and lat_values[0] > lat_values[-1]:
                    subset = subset.sel({lat_coord: slice(bbox.north, bbox.south)})
                else:
                    subset = subset.sel({lat_coord: slice(bbox.south, bbox.north)})

            lon_coord = self._coord_name(subset, ("lon", "longitude"))
            if lon_coord:
                west = bbox.west if bbox.west >= 0 else bbox.west + 360
                east = bbox.east if bbox.east >= 0 else bbox.east + 360
                lo, hi = (west, east) if west <= east else (east, west)
                subset = subset.sel({lon_coord: slice(lo, hi)})

            isel_args = {}
            if lat_coord:
                isel_args[lat_coord] = slice(None, None, stride)
            if lon_coord:
                isel_args[lon_coord] = slice(None, None, stride)
            if isel_args:
                subset = subset.isel(**isel_args)

            data = {name: subset[name].fillna(0).astype("float32").values.tolist() for name in variables if name in subset}

            source_time = self._safe_dt(subset.coords[time_coord].values if time_coord else None)
            self._last_fetch_at = datetime.now(timezone.utc)
            self._last_error = None
            log.info(
                "subset fetch success engine=%s vars=%d bbox=%s stride=%s valid_time=%s",
                self._open_engine,
                len(data),
                bbox.as_list(),
                stride,
                valid_time,
            )
            return data, source_time
        except Exception as exc:
            self._last_error = str(exc)
            log.error(
                "subset fetch failed engine=%s url=%s bbox=%s stride=%s valid_time=%s err=%s",
                self._open_engine,
                self.dataset_url,
                bbox.as_list(),
                stride,
                valid_time,
                exc,
                exc_info=True,
            )
            raise ProviderUnavailableError(str(exc), provider="thredds_gfs") from exc

    def health(self) -> dict[str, Any]:
        opened_age_s = None
        if self._opened_at:
            opened_age_s = int((datetime.now(timezone.utc) - self._opened_at).total_seconds())
        return {
            "provider": "thredds_gfs",
            "dataset_url": self.dataset_url,
            "dataset_open": self._dataset is not None,
            "open_engine": self._open_engine,
            "opened_at": self._opened_at.isoformat().replace("+00:00", "Z") if self._opened_at else None,
            "opened_age_seconds": opened_age_s,
            "last_fetch_at": self._last_fetch_at.isoformat().replace("+00:00", "Z") if self._last_fetch_at else None,
            "last_error": self._last_error,
            "dependencies": self._dependencies(),
        }
