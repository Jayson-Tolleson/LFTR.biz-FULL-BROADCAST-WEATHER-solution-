from pathlib import Path


def test_watch_live_autoplay_muted_with_unmute_control_and_state_requests():
    src = Path('static/js/watch.js').read_text(encoding='utf-8')
    assert 'setLiveMutedAutoplay' in src
    assert 'v.muted = true' in src
    assert "unmuteBtn.textContent = 'Tap for sound'" in src
    assert "if (present) {" in src and 'requestStream();' in src
    assert "if (present && !requestPending)" in src


def test_gfs_bootstrap_stage_order_fish_then_balloons_then_weather():
    html = Path('static/indexgfs.html').read_text(encoding='utf-8')
    fish_idx = html.index("[gfs] bootstrap stage: fish start")
    balloons_idx = html.index("[gfs] bootstrap stage: balloons start")
    weather_idx = html.index("[gfs] bootstrap stage: weather start")
    assert fish_idx < balloons_idx < weather_idx
    assert 'await loadFish();' in html
    assert 'await renderJetBalloons();' in html
    assert 'await loadClouds(true);' in html
    assert '[gfs] weather bootstrap failed, fish/balloons preserved' in html


def test_gfs_marker_render_defers_without_maps_libraries():
    html = Path('static/indexgfs.html').read_text(encoding='utf-8')
    assert "fish markers deferred until maps3d/marker libraries are ready" in html
    assert 'state.markerRetryTimer = setTimeout' in html


def test_gfs_scene_200_partial_payload_path_is_nonfatal_and_diagnostic():
    html = Path('static/indexgfs.html').read_text(encoding='utf-8')
    assert 'scene payload parsed but contains no weather arrays; keeping fish/balloons active' in html
    assert 'schema failure url=' in html
    assert 'content-type=' in html
