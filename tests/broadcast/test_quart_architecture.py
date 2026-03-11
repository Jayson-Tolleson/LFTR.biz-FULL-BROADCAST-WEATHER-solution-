from __future__ import annotations

from pathlib import Path

import pytest


def test_create_app_returns_quart_instance():
    quart = pytest.importorskip('quart')
    from server.app_factory import create_app

    app = create_app()
    assert isinstance(app, quart.Quart)


def test_gfs_routes_and_ws_channel_registered():
    from server.app_factory import create_app

    app = create_app()
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/gfs/api/weather' in paths
    assert '/gfs/api/clouds' in paths
    assert '/gfs/api/bait' in paths
    assert '/gfs/api/health' in paths
    assert '/ws/gfs' in paths


def test_quart_startup_static_missing_guard(monkeypatch, tmp_path):
    from server import app_factory as app_factory_module

    missing_static = tmp_path / 'missing_static'
    monkeypatch.setattr(app_factory_module, 'STATIC_DIR', missing_static)
    monkeypatch.setattr(app_factory_module, 'TEMPLATES_DIR', missing_static)

    with pytest.raises(RuntimeError, match='static directory missing at startup'):
        app_factory_module.create_app()


def test_readme_mentions_quart_and_ws_scope():
    text = Path('README.md').read_text(encoding='utf-8').lower()
    assert 'quart' in text
    assert '/gfs' in text
    assert '/ws/watch' in text


def test_root_route_returns_html_200():
    import asyncio
    from server.app_factory import create_app

    async def _run():
        app = create_app()
        client = app.test_client()
        resp = await client.get('/')
        assert resp.status_code == 200

    asyncio.run(_run())


def test_page_routes_return_quart_responses():
    import asyncio
    from server.app_factory import create_app

    async def _run():
        app = create_app()
        client = app.test_client()
        for path in ['/', '/broadcast', '/watch', '/gfs', '/gfs/', '/status-dashboard']:
            resp = await client.get(path)
            assert resp.status_code == 200
            body = await resp.get_data(as_text=True)
            assert isinstance(body, str)
            assert 'coroutine' not in body.lower()

    asyncio.run(_run())


def test_missing_static_returns_controlled_500(monkeypatch, tmp_path):
    import asyncio
    from server import routes as routes_module
    from server.app_factory import create_app

    static_dir = tmp_path / 'static'
    static_dir.mkdir(parents=True)
    (static_dir / 'broadcast.html').write_text('ok', encoding='utf-8')
    (static_dir / 'watch.html').write_text('ok', encoding='utf-8')
    (static_dir / 'indexgfs.html').write_text('ok', encoding='utf-8')
    (static_dir / 'status_dashboard.html').write_text('ok', encoding='utf-8')
    monkeypatch.setattr(routes_module, 'STATIC_DIR', static_dir)

    async def _run():
        app = create_app()
        client = app.test_client()
        resp = await client.get('/')
        assert resp.status_code == 500
        payload = await resp.get_json()
        assert payload['error'] == 'static deployment invalid'

    asyncio.run(_run())
