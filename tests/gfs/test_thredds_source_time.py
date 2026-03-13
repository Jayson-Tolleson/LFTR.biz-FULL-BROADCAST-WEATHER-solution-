from datetime import datetime, timezone

import numpy as np
import xarray as xr

from server.gfs.models import BBox
from server.gfs.providers.thredds_gfs import ThreddsGfsProvider


def _provider_with_dataset(ds):
    provider = ThreddsGfsProvider("https://example.invalid")
    provider._discover_var_names_sync = lambda requested: {"wind_u": "u"}  # type: ignore[attr-defined]
    provider._fetch_ncss_bytes_sync = lambda _url: b"unused"  # type: ignore[attr-defined]
    provider._open_ncss_dataset_sync = lambda _payload: ds  # type: ignore[attr-defined]
    return provider


def test_source_time_preserves_present_selector_when_valid_time_none():
    ds = xr.Dataset(
        {"u": (("time", "lat", "lon"), np.array([[[1.0, 2.0], [3.0, 4.0]]], dtype=float))},
        coords={"time": [np.datetime64("2026-03-13T00:00:00")], "lat": [1.0, 2.0], "lon": [3.0, 4.0]},
    )
    p = _provider_with_dataset(ds)
    data, _resolved_dt = p._fetch_subset_sync(variables=("wind_u",), bbox=BBox(-10, 0, 10, 10), stride=1, valid_time=None)
    assert data["source_time"] == "present"
    assert isinstance(data["resolved_time"], str)
    assert data["resolved_time"].endswith("Z")
    assert data["dataset_time"] == "2026-03-13T00:00:00Z"


def test_source_time_uses_present_even_with_explicit_valid_time():
    ds = xr.Dataset(
        {"u": (("lat", "lon"), np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float))},
        coords={"lat": [1.0, 2.0], "lon": [3.0, 4.0]},
    )
    p = _provider_with_dataset(ds)
    vt = datetime(2026, 3, 13, 1, 2, 3, tzinfo=timezone.utc)
    data, _resolved_dt = p._fetch_subset_sync(variables=("wind_u",), bbox=BBox(-10, 0, 10, 10), stride=1, valid_time=vt)
    assert data["source_time"] == "present"
    assert isinstance(data["resolved_time"], str)
    assert data["resolved_time"].endswith("Z")
    assert data["dataset_time"] is None
