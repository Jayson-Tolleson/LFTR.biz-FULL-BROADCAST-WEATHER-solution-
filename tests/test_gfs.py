from server.gfs_service import generate_live_payload


def grid(rows, cols):
    return [[0] * cols for _ in range(rows)]


def test_grid_mismatch_named_diag():
    out = generate_live_payload(grid(721, 1440), grid(241, 480), allow_synthetic_fallback=False)
    assert not out["ok"]
    assert "precip" in out["error"]


def test_fallback_gate():
    out = generate_live_payload(grid(721, 1440), grid(241, 480), allow_synthetic_fallback=True)
    assert out["diagnostics"]["data_source"] == "synthetic_fallback"
