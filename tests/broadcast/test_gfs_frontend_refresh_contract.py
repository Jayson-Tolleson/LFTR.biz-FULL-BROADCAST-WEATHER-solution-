from pathlib import Path


def test_main_overlay_refresh_discards_stale_and_aborts_old_requests():
    src = Path('static/js/gfs/main.js').read_text(encoding='utf-8')
    assert 'activeAbort.abort()' in src
    assert 'stale response discarded' in src
    assert 'seq !== overlayState.requestSeq' in src


def test_main_fetches_base_then_advanced_bait_layer():
    src = Path('static/js/gfs/main.js').read_text(encoding='utf-8')
    assert '/gfs/api/bait?bbox=' in src
    assert '/gfs/api/bait/advanced?bbox=' in src
    assert 'advanced bait replaced base' in src
    assert 'installTimerRefresh' in src


def test_overlay_renderers_use_compact_polygon_field_contract_first():
    for path in [
        'static/js/gfs/cloud-zones.js',
        'static/js/gfs/rain-zones.js',
        'static/js/gfs/bait-zones.js',
    ]:
        src = Path(path).read_text(encoding='utf-8')
        assert 'polygon_field_v1' in src
        assert 'normalizePolygonFieldPayload' in src


def test_bait_scoring_module_has_tunable_heuristic_formulas():
    src = Path('static/js/gfs/bait_scoring.js').read_text(encoding='utf-8')
    assert 'computeBaseBaitScore' in src
    assert 'computeFinalBaitScore' in src
    assert '0.22 * windScore' in src
    assert '0.40 * baseBait' in src
