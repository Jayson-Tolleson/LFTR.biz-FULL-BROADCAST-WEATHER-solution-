from __future__ import annotations

from datetime import datetime
from typing import Any

from server.gfs.errors import ProviderUnavailableError
from server.gfs.models import BBox

import xarray as xr


class ThreddsGfsProvider:
    """THREDDS FMRC Best OPeNDAP provider with bounded subset retrieval."""

    def __init__(self, dataset_url: str) -> None:
        self.dataset_url = dataset_url
        self._dataset = None
        self._opened_at: datetime | None = None

    def _open_dataset(self):
        if self._dataset is not None:
            return self._dataset
        try:
            self._dataset = xr.open_dataset(self.dataset_url, engine="netcdf4")
            self._opened_at = datetime.utcnow()
        except Exception as primary_exc:
            try:
                self._dataset = xr.open_dataset(self.dataset_url, engine="pydap")
                self._opened_at = datetime.utcnow()
            except Exception as fallback_exc:
                raise ProviderUnavailableError(
                    f"failed to open dataset via netcdf4/pydap: {primary_exc}; {fallback_exc}",
                    provider="thredds_gfs",
                ) from fallback_exc
        return self._dataset

    async def fetch_subset(self, *, variables: tuple[str, ...], bbox: BBox, stride: int, valid_time: datetime | None) -> tuple[dict[str, Any], datetime | None]:
        ds = self._open_dataset()
        try:
            subset = ds[list(variables)]
            if valid_time is not None and "time" in subset.coords:
                subset = subset.sel(time=valid_time, method="nearest")
            if "lat" in subset.coords:
                subset = subset.sel(lat=slice(bbox.south, bbox.north))
            if "lon" in subset.coords:
                west = bbox.west if bbox.west >= 0 else bbox.west + 360
                east = bbox.east if bbox.east >= 0 else bbox.east + 360
                lo, hi = (west, east) if west <= east else (east, west)
                subset = subset.sel(lon=slice(lo, hi))
            subset = subset.isel(lat=slice(None, None, stride), lon=slice(None, None, stride))
            data = {name: subset[name].fillna(0).astype("float32").values.tolist() for name in variables if name in subset}
            source_time = None
            if "time" in subset.coords:
                t = subset.coords["time"].values
                source_time = datetime.utcfromtimestamp(t.astype("datetime64[s]").astype(int))
            return data, source_time
        except Exception as exc:
            raise ProviderUnavailableError(str(exc), provider="thredds_gfs") from exc

    def health(self) -> dict[str, Any]:
        return {
            "provider": "thredds_gfs",
            "dataset_url": self.dataset_url,
            "dataset_open": self._dataset is not None,
            "opened_at": self._opened_at.isoformat() + "Z" if self._opened_at else None,
        }
