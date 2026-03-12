from pathlib import Path


def test_bait_zone_uses_probability_color_ramp_and_path_api():
    src = Path('static/js/gfs/bait-zones.js').read_text(encoding='utf-8')
    assert 'probabilityColorRamp' in src
    assert 'el.path = path' in src
    assert 'gmp-polyline-3d' in src
    assert 'gmp-polygon-3d' in src


def test_polygon_math_has_ocean_signal_filtering():
    src = Path('static/js/gfs/polygon_math.js').read_text(encoding='utf-8')
    assert 'hasBelievableOceanSignal' in src
    assert 'sst >= -2.5 && sst <= 38.5' in src
    assert 'Math.hypot(current_u, current_v) >= 0.03' in src
