from __future__ import annotations

from pathlib import Path


def test_fish_orb_renderer_module_has_batched_interfaces():
    src = Path('static/js/fish_orb_renderer.js').read_text(encoding='utf-8')
    assert 'confidenceToOrbStyle' in src
    assert 'clusterFishLights' in src
    assert 'maxActiveOrbsForRange' in src
    assert 'active: new Map()' in src
    assert 'free: []' in src
    assert 'requestAnimationFrame' in src
    assert "document.createElement('div')" not in src


def test_fish_orb_confidence_mapping_and_palette_present():
    src = Path('static/js/fish_orb_renderer.js').read_text(encoding='utf-8')
    assert '0.5 + c * 2.5' in src  # pulse speed
    assert '0.2 + c * 0.8' in src  # brightness mapping
    assert '80, 255, 140' in src  # elite palette
    assert '0, 255, 120' in src   # high palette
    assert '60, 220, 90' in src   # medium palette
    assert '40, 120, 40' in src   # low palette


def test_indexgfs_uses_fish_orb_renderer_main_path_not_per_point_markers():
    html = Path('static/indexgfs.html').read_text(encoding='utf-8')
    assert '/static/js/fish_orb_renderer.js' in html
    assert 'window.LFTRFishOrbRenderer.createRenderer' in html
    assert 'state.fishOrbRenderer.rebuild(true)' in html
    render_start = html.index('async function renderMapMarkers()')
    load_start = html.index('async function loadFish()')
    render_block = html[render_start:load_start]
    assert 'state.fish.forEach' not in render_block
    assert 'setInterval(() => {' in render_block


def test_fish_orb_renderer_uses_interactive_pin_content_and_click_events():
    src = Path('static/js/fish_orb_renderer.js').read_text(encoding='utf-8')
    assert 'new ctx.maps3dLib.Marker3DInteractiveElement' in src
    assert 'new ctx.markerLib.PinElement' in src
    assert 'marker.append(pin)' in src or 'wrapper.marker.append(wrapper.pin)' in src
    assert 'gmp-click' in src
    assert 'ctx.openHud' in src
