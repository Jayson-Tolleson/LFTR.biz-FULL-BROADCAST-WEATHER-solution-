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


class GFSDecoder:
    """Decode core atmospheric fields from GRIB2 through cfgrib."""

    def decode(self, grib_path: Path) -> DecodedFields | None:
        if xr is None or np is None:
            return None
        ds = xr.open_dataset(grib_path, engine="cfgrib", backend_kwargs={"indexpath": ""})
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
