from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding='utf-8')


def test_greek_math_module_exports_ascii_api():
    src = _read('static/js/gfs/greek_math.js')
    for fn in ['degToRad', 'radToDeg', 'wrappedLongitude', 'bearingFromUV', 'cellOffsets']:
        assert f'export function {fn}' in src


def test_polygon_math_module_present_and_uses_greek_internals():
    src = _read('static/js/gfs/polygon_math.js')
    for fn in ['normalizePolygonFeature', 'buildCellRing', 'buildOrientedCellRing', 'buildRingSet']:
        assert f'export function {fn}' in src
    # Greek notation stays inside contained math module internals.
    assert 'φ' in src
    assert 'λ' in src


def test_polygon_render_output_keys_ascii_safe():
    src = _read('static/js/gfs/polygon_render.js')
    assert 'lat' in src
    assert 'lng' in src
    assert 'altitude' in src
    # Keep Greek identifiers out of the ASCII-safe rendering adapter.
    assert 'φ' not in src
    assert 'λ' not in src


def test_overlay_modules_use_polygon_math_boundary():
    for path in [
        'static/js/gfs/cloud-zones.js',
        'static/js/gfs/rain-zones.js',
        'static/js/gfs/bait-zones.js',
    ]:
        src = _read(path)
        assert 'polygon_math' in src
        assert 'polygon_render' in src
