from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding='utf-8')


def test_gfs_page_has_no_static_live_overlay_dom_node():
    html = _read('static/indexgfs.html')
    assert 'id="liveOverlay"' not in html
    assert 'id="livePreview"' not in html


def test_live_overlay_module_exists_and_exports_lifecycle_api():
    src = _read('static/js/ui/liveOverlay.js')
    assert 'export function createLiveOverlay' in src
    assert 'export function destroyLiveOverlay' in src
    assert "document.getElementById('liveOverlay')" in src


def test_watch_socket_handles_broadcaster_start_stop_overlay_lifecycle():
    src = _read('static/js/watch.js')
    assert "msg.type === 'broadcaster-start'" in src
    assert "msg.type === 'broadcaster-stop'" in src
    assert "import('/static/js/ui/liveOverlay.js')" in src


def test_broadcast_socket_emits_broadcaster_start_and_stop_events():
    src = _read('server/broadcast/routes.py')
    assert '"type": "broadcaster-start"' in src
    assert '"type": "broadcaster-stop"' in src
