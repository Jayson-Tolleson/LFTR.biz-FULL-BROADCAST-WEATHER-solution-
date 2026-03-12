from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from server.app_factory import create_app
from server.gfs.greek_math import bearing_from_uv, cell_offsets, deg_to_rad, rad_to_deg, wrapped_longitude
from server.gfs.models import BAIT_ATMOSPHERIC_VARIABLES, CLOUD_VARIABLES, WEATHER_VARIABLES, BBox
from server.gfs.providers.thredds_gfs import DEFAULT_NCSS_GRID_URL, ThreddsGfsProvider


def test_backend_greek_math_roundtrip_and_helpers():
    phi, lam = deg_to_rad(34.0, -118.0)
    lat, lon = rad_to_deg(phi, lam)
    assert abs(lat - 34.0) < 1e-9
    assert abs(lon + 118.0) < 1e-9

    wrapped = wrapped_longitude(4.0)
    assert -3.14159 <= wrapped < 3.14159

    theta = bearing_from_uv(1.0, 0.0)
    assert abs(theta - 1.57079632679) < 1e-6

    dphi, dlam = cell_offsets(0.25)
    assert dphi > 0
    assert dlam > 0


def test_ncss_url_uses_grid_twod_base_not_best_path():
    provider = ThreddsGfsProvider("https://thredds.ucar.edu/thredds/dodsC/grib/NCEP/GFS/Global_0p25deg/Best")
    url = provider._build_ncss_url_sync(
        var_names=["Precipitation_rate_surface", "Total_cloud_cover_entire_atmosphere"],
        bbox=BBox(-120.0, 30.0, -110.0, 40.0),
        stride=4,
    )
    assert DEFAULT_NCSS_GRID_URL in url
    assert "/thredds/ncss/grid/grib/NCEP/GFS/Global_0p25deg/TwoD" in url
    assert "/thredds/ncss/grib/NCEP/GFS/Global_0p25deg/Best" not in url


def test_ncss_url_builder_contains_required_query_params_and_repeated_var():
    provider = ThreddsGfsProvider("ignored")
    url = provider._build_ncss_url_sync(
        var_names=["A", "B"],
        bbox=BBox(-120.0, 30.0, -110.0, 40.0),
        stride=4,
    )
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for k in ["north", "south", "west", "east", "time", "horizStride", "accept", "var"]:
        assert k in qs
    assert qs["time"] == ["present"]
    assert qs["horizStride"] == ["4"]
    assert qs["accept"] == ["netCDF4"]
    assert qs["var"] == ["A", "B"]


def test_first_pass_weather_cloud_bait_variable_sets_are_lean_and_pinned():
    assert WEATHER_VARIABLES == (
        "wind_u",
        "wind_v",
        "air_temp",
        "rel_humidity",
        "dewpoint",
        "pressure_msl",
    )
    assert CLOUD_VARIABLES == (
        "cloud_total",
        "cloud_low",
        "cloud_mid",
        "cloud_high",
    )
    assert BAIT_ATMOSPHERIC_VARIABLES == (
        "wind_u",
        "wind_v",
        "air_temp",
        "rel_humidity",
        "dewpoint",
        "pressure_msl",
        "precip_rate",
        "cloud_total",
    )


def test_provider_parse_path_is_in_memory_only_no_temp_files():
    src = open("server/gfs/providers/thredds_gfs.py", "r", encoding="utf-8").read()
    assert "io.BytesIO(payload_bytes)" in src
    assert "with open(" not in src
    assert ".tmp" not in src


def _assert_atmos_contract(payload: dict):
    pf = payload.get("polygon_field_v1")
    assert isinstance(pf, dict)
    assert pf.get("schema") == "gfs_polygon_field_v1"
    assert isinstance(pf.get("fields"), dict)
    for key in [
        "lat", "lon", "altitude_m", "wind_u", "wind_v", "air_temp", "rel_humidity", "dewpoint",
        "pressure_msl",
    ]:
        assert key in pf["fields"]
        assert isinstance(pf["fields"][key], list)


def test_weather_contains_compact_columnar_contract():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get('/gfs/api/weather?bbox=-140,20,-100,50&quality=coarse')
        if res.status_code == 503:
            return
        assert res.status_code == 200
        payload = await res.get_json()
        _assert_atmos_contract(payload)

    asyncio.run(_run())


def test_bait_base_and_advanced_contracts_present():
    async def _run():
        app = create_app()
        client = app.test_client()

        base_res = await client.get('/gfs/api/bait?bbox=-140,20,-100,50&quality=coarse')
        if base_res.status_code != 503:
            assert base_res.status_code == 200
            base = await base_res.get_json()
            assert base.get('bait_base_field_v1', {}).get('schema') == 'gfs_bait_field_v1'
            assert base.get('bait_advanced_field_v1') in (None, {})
            bfields = base['bait_base_field_v1']['fields']
            for key in ['wind_u', 'wind_v', 'air_temp', 'rel_humidity', 'dewpoint', 'pressure_msl', 'precip_rate', 'cloud_total']:
                assert isinstance(bfields.get(key), list)

        adv_res = await client.get('/gfs/api/bait/advanced?bbox=-140,20,-100,50&quality=coarse')
        if adv_res.status_code != 503:
            assert adv_res.status_code == 200
            adv = await adv_res.get_json()
            assert adv.get('bait_base_field_v1', {}).get('schema') == 'gfs_bait_field_v1'
            assert adv.get('bait_advanced_field_v1', {}).get('schema') == 'bait_ocean_field_v1'
            fields = adv['bait_advanced_field_v1']['fields']
            for key in ['lat', 'lon', 'sst', 'chlorophyll', 'current_u', 'current_v', 'water_color_index', 'optional_ssh_anomaly']:
                assert isinstance(fields.get(key), list)

    asyncio.run(_run())
