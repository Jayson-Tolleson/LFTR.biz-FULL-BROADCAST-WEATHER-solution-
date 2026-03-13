from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

import numpy as np
import xarray as xr

from server.gfs.errors import ProviderUnavailableError
from server.gfs.models import BBox
from server.gfs.serializers import iso_utc


log = logging.getLogger("server.gfs.provider.thredds")

# Active NCSS Grid TwoD endpoint for live atmospheric fetches.
DEFAULT_NCSS_GRID_URL = "https://thredds.ucar.edu/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/TwoD"

VAR_ALIASES: dict[str, tuple[str, ...]] = {
    "wind_u": (
        "u-component_of_wind_height_above_ground",
        "u-component_of_wind_height_above_ground_10m",
    ),
    "wind_v": (
        "v-component_of_wind_height_above_ground",
        "v-component_of_wind_height_above_ground_10m",
    ),
    "air_temp": ("Temperature_height_above_ground",),
    "rel_humidity": ("Relative_humidity_height_above_ground",),
    "dewpoint": ("Dewpoint_temperature_height_above_ground",),
    "pressure_msl": ("Pressure_reduced_to_MSL_msl",),
    "precip_rate": ("Precipitation_rate_surface",),
    "cloud_total": ("Total_cloud_cover_entire_atmosphere",),
    "cloud_low": ("Low_cloud_cover_low_cloud",),
    "cloud_mid": ("Medium_cloud_cover_middle_cloud",),
    "cloud_high": ("High_cloud_cover_high_cloud",),
}


class ThreddsGfsProvider:
    """THREDDS provider using NCSS Grid TwoD present-time fetch for live subsets."""

    def __init__(self, dataset_url: str, *, fetch_timeout_s: float = 20.0, ncss_grid_url: str = DEFAULT_NCSS_GRID_URL) -> None:
        self.dataset_url = dataset_url
        self.fetch_timeout_s = fetch_timeout_s
        self.ncss_grid_url = ncss_grid_url
        self._dataset = None
        self._opened_at: datetime | None = None
        self._open_engine: str | None = None
        self._last_error: str | None = None
        self._last_fetch_at: datetime | None = None
        self._discovered_var_map: dict[str, str] | None = None

    def _dependencies(self) -> dict[str, bool]:
        return {
            "netCDF4": importlib.util.find_spec("netCDF4") is not None,
            "pydap": importlib.util.find_spec("pydap") is not None,
            "h5netcdf": importlib.util.find_spec("h5netcdf") is not None,
        }

    def _metadata_url(self) -> str:
        parsed = urllib.parse.urlparse(self.ncss_grid_url)
        path = parsed.path.replace("/ncss/grid/", "/dodsC/")
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))

    def _open_dataset_sync(self):
        if self._dataset is not None:
            return self._dataset

        deps = self._dependencies()
        metadata_url = self._metadata_url()
        log.info("opening metadata dataset url=%s deps=%s", metadata_url, deps)

        for engine in ("netcdf4", "pydap"):
            try:
                self._dataset = xr.open_dataset(metadata_url, engine=engine)
                self._opened_at = datetime.now(timezone.utc)
                self._open_engine = engine
                self._last_error = None
                log.info("metadata open success engine=%s", engine)
                return self._dataset
            except Exception as exc:
                self._last_error = f"metadata open failed engine={engine} err={exc}"
                log.warning("metadata open failed engine=%s err=%s", engine, exc)

        raise ProviderUnavailableError(
            f"failed metadata open via netcdf4/pydap: {self._last_error}",
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

    def _discover_var_names_sync(self, requested: tuple[str, ...]) -> dict[str, str]:
        if self._discovered_var_map is not None:
            return {k: v for k, v in self._discovered_var_map.items() if k in requested}

        ds = self._open_dataset_sync()
        data_vars = set(ds.data_vars.keys())
        mapping: dict[str, str] = {}
        for internal_name, candidates in VAR_ALIASES.items():
            for var_name in candidates:
                if var_name in data_vars:
                    mapping[internal_name] = var_name
                    break

        self._discovered_var_map = mapping
        return {k: v for k, v in mapping.items() if k in requested}

    def _build_ncss_params_sync(self, *, var_names: list[str], bbox: BBox, stride: int) -> list[tuple[str, str]]:
        query: list[tuple[str, str]] = [
            ("north", str(bbox.north)),
            ("south", str(bbox.south)),
            ("west", str(bbox.west)),
            ("east", str(bbox.east)),
            # NCSS live fetches must always request nearest indexed time.
            ("time", "present"),
            ("accept", "netCDF4"),
            ("addLatLon", "true"),
            ("horizStride", str(max(1, int(stride)))),
        ]
        for var_name in var_names:
            query.append(("var", var_name))
        return query

    def _build_ncss_url_sync(self, *, var_names: list[str], bbox: BBox, stride: int) -> str:
        query = self._build_ncss_params_sync(var_names=var_names, bbox=bbox, stride=stride)
        log.debug("ncss params bbox=%s stride=%s time=present vars=%s", bbox.as_list(), stride, var_names)
        return f"{self.ncss_grid_url}?{urllib.parse.urlencode(query)}"

    @staticmethod
    def _requested_time_selector(_valid_time: datetime | None) -> str:
        return "present"

    def _fetch_ncss_bytes_sync(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"Accept": "application/x-netcdf"})
        with urllib.request.urlopen(req, timeout=self.fetch_timeout_s) as res:
            return res.read()

    def _open_ncss_dataset_sync(self, payload_bytes: bytes):
        with io.BytesIO(payload_bytes) as buf:
            try:
                return xr.open_dataset(buf, engine="h5netcdf")
            except Exception:
                pass

        netcdf4_mod = __import__("netCDF4")
        nc = netcdf4_mod.Dataset("inmemory.nc", memory=payload_bytes)
        return xr.open_dataset(xr.backends.NetCDF4DataStore(nc))

    @staticmethod
    def _to_2d_float32(da: xr.DataArray) -> list[list[float]]:
        work = da
        lat_like = {"lat", "latitude"}
        lon_like = {"lon", "longitude"}
        for dim in list(work.dims):
            if dim not in lat_like and dim not in lon_like:
                work = work.isel({dim: 0})
        arr = work.fillna(0).astype("float32").values
        if arr.ndim == 1:
            arr = arr.reshape(arr.shape[0], 1)
        return arr.tolist()

    def _fetch_subset_sync(
        self,
        *,
        variables: tuple[str, ...],
        bbox: BBox,
        stride: int,
        valid_time: datetime | None,
    ) -> tuple[dict[str, Any], datetime | None]:
        requested_time_selector = self._requested_time_selector(valid_time)
        resolved = self._discover_var_names_sync(variables)
        if not resolved:
            raise ValueError("no requested atmospheric variables were resolved from dataset metadata")

        url = self._build_ncss_url_sync(var_names=list(resolved.values()), bbox=bbox, stride=stride)
        payload_bytes = self._fetch_ncss_bytes_sync(url)
        ds = self._open_ncss_dataset_sync(payload_bytes)

        data: dict[str, Any] = {}
        for internal_name, upstream_name in resolved.items():
            if upstream_name not in ds.data_vars:
                continue
            data[internal_name] = self._to_2d_float32(ds[upstream_name])

        time_coord = self._coord_name(ds, ("time", "valid_time"))
        source_time = self._safe_dt(ds.coords[time_coord].values if time_coord else None)
        data["source_time"] = requested_time_selector
        data["resolved_time"] = iso_utc(source_time)
        self._last_fetch_at = datetime.now(timezone.utc)
        self._last_error = None
        return data, source_time

    async def fetch_subset(
        self,
        *,
        variables: tuple[str, ...],
        bbox: BBox,
        stride: int,
        valid_time: datetime | None,
    ) -> tuple[dict[str, Any], datetime | None]:
        try:
            resolved = self._discover_var_names_sync(variables)
            requested_time_selector = self._requested_time_selector(valid_time)
            url = self._build_ncss_url_sync(var_names=list(resolved.values()), bbox=bbox, stride=stride)
            data, source_time = await asyncio.wait_for(
                asyncio.to_thread(
                    self._fetch_subset_sync,
                    variables=variables,
                    bbox=bbox,
                    stride=stride,
                    valid_time=valid_time,
                ),
                timeout=self.fetch_timeout_s,
            )
            log.info(
                "ncss subset fetch success url=%s vars=%s bbox=%s stride=%s source_time=%s resolved_time=%s",
                url,
                list(resolved.keys()),
                bbox.as_list(),
                stride,
                data.get("source_time"),
                data.get("resolved_time"),
            )
            return data, source_time
        except Exception as exc:
            self._last_error = str(exc)
            log.error(
                "ncss subset fetch failed base_url=%s bbox=%s stride=%s vars=%s err=%s",
                self.ncss_grid_url,
                bbox.as_list(),
                stride,
                list(variables),
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
            "ncss_url": self.ncss_grid_url,
            "metadata_url": self._metadata_url(),
            "metadata_open": self._dataset is not None,
            "open_engine": self._open_engine,
            "opened_at": self._opened_at.isoformat().replace("+00:00", "Z") if self._opened_at else None,
            "opened_age_seconds": opened_age_s,
            "last_fetch_at": self._last_fetch_at.isoformat().replace("+00:00", "Z") if self._last_fetch_at else None,
            "last_error": self._last_error,
            "dependencies": self._dependencies(),
        }
