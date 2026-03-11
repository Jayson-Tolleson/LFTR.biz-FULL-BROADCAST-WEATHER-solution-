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
