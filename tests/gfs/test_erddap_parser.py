from server.gfs.providers.erddap_csv import parse_erddap_grid


def test_parse_erddap_grid_uses_named_columns_and_builds_lon_axis():
    text = "time,longitude,latitude,sst\n2024-01-01T00:00:00Z,-121,34,20.5\n2024-01-01T00:00:00Z,-120,34,21.0\n"
    grid, diag = parse_erddap_grid(text, preferred_value_columns=("sst",))
    assert len(grid) == 1
    assert len(grid[0]) == 2
    assert diag.lat_count == 1
    assert diag.lon_count == 2


def test_parse_erddap_grid_reports_rejected_rows():
    text = "time,latitude,longitude,chlorophyll\nunits,degrees_north,degrees_east,mg m-3\n"
    grid, diag = parse_erddap_grid(text, preferred_value_columns=("chlorophyll",))
    assert grid == []
    assert diag.parser_rejected_rows >= 1
