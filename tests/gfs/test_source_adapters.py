from datetime import datetime, timezone

from server.gfs.providers.adapters import Viewport, build_erddap_subset_request, build_ncss_subset_request, split_antimeridian


def test_ncss_adapter_includes_bbox_stride_time_vars():
    vp = Viewport(west=-130, south=20, east=-110, north=35)
    url = build_ncss_subset_request(vp, ["A", "B"], 4, None, "https://example/ncss")
    assert "north=35" in url
    assert "south=20" in url
    assert "west=-130" in url
    assert "east=-110" in url
    assert "horizStride=4" in url
    assert "time=present" in url
    assert "var=A" in url and "var=B" in url


def test_ncss_adapter_forces_present_even_with_explicit_valid_time():
    vp = Viewport(west=-130, south=20, east=-110, north=35)
    url = build_ncss_subset_request(vp, ["A"], 1, datetime(2026, 3, 13, tzinfo=timezone.utc), "https://example/ncss")
    assert "time=present" in url
    assert "2026-" not in url


def test_erddap_adapter_uses_dap_dimension_constraints_and_antimeridian_split():
    vp = Viewport(west=170, south=-10, east=-170, north=10)
    parts = split_antimeridian(vp)
    assert len(parts) == 2
    urls = build_erddap_subset_request(vp, "https://example/erddap.csv", ["sst"], 2, None)
    assert len(urls) == 2
    assert "sst=%5Blast%5D%5B%2810" not in urls[0]  # sanity: encoded constraints present
    assert "%5Blast%5D" in urls[0]
    assert "%3A2%3A" in urls[0]
