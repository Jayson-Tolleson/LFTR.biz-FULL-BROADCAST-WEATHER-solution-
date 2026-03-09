from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import numpy as np
except Exception:
    np = None

try:
    import xarray as xr
except Exception:
    xr = None


@dataclass
class DecodedFields:
    lat: Any
    lon: Any
    temperature: Any
    humidity: Any
    wind_u: Any
    wind_v: Any
    cloud_fraction: Any
    precip_rate: Any


def open_gfs_groups(grib_path: Path) -> dict[str, Any]:
    datasets: dict[str, Any] = {}
    if xr is None:
        return datasets

    try:
        datasets["surface"] = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}, "indexpath": ""},
        )
    except Exception:
        pass

    try:
        datasets["2m"] = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}, "indexpath": ""},
        )
    except Exception:
        pass

    try:
        datasets["10m"] = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}, "indexpath": ""},
        )
    except Exception:
        pass

    try:
        datasets["isobaric"] = xr.open_dataset(
            grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "isobaricInhPa"}, "indexpath": ""},
        )
    except Exception:
        pass

    return datasets


class GFSDecoder:
    """Decode core atmospheric fields from GRIB2 through cfgrib."""

    def decode(self, grib_path: Path) -> DecodedFields | None:
        if xr is None or np is None:
            return None
        groups = open_gfs_groups(grib_path)
        ds = groups.get("surface")
        if ds is None:
            ds = groups.get("2m")
        if ds is None:
            ds = groups.get("10m")
        if ds is None:
            ds = groups.get("isobaric")
        if ds is None:
            return None
        lat = ds.coords.get("latitude")
        lon = ds.coords.get("longitude")
        if lat is None or lon is None:
            return None
        lat_arr = np.asarray(lat.values)
        lon_arr = np.asarray(lon.values)
        if lat_arr.ndim == 1 and lon_arr.ndim == 1:
            lon2, lat2 = np.meshgrid(lon_arr, lat_arr)
        else:
            lat2, lon2 = lat_arr, lon_arr
        def first(*names: str):
            for n in names:
                if n in ds:
                    arr = ds[n]
                    for dim in ("time", "step", "isobaricInhPa", "heightAboveGround"):
                        if dim in arr.dims:
                            arr = arr.isel({dim: 0})
                    return np.asarray(arr.values, dtype=float)
            return None
        fields = DecodedFields(
            lat=lat2,
            lon=lon2,
            temperature=first("t", "TMP"),
            humidity=first("r", "RH"),
            wind_u=first("u", "UGRD"),
            wind_v=first("v", "VGRD"),
            cloud_fraction=first("tcc", "TCDC"),
            precip_rate=first("prate", "PRATE"),
        )
        try:
            ds.close()
        except Exception:
            pass
        return fields
