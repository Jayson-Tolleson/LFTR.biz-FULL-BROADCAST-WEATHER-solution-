from __future__ import annotations

from pathlib import Path

import pytest


def test_create_app_returns_quart_instance():
    quart = pytest.importorskip('quart')
    from server.app_factory import create_app

    app = create_app()
    assert isinstance(app, quart.Quart)


def test_gfs_is_http_only_no_ws_gfs_route():
    from server.app_factory import create_app

    app = create_app()
    paths = {rule.rule for rule in app.url_map.iter_rules()}
    assert '/api/gfs/scene' in paths
    assert '/api/gfs/status' in paths
    assert '/api/gfs/cloud-tiles' in paths
    assert '/api/gfs/hazards' in paths
    assert '/api/gfs/diagnostics' in paths
    assert '/ws/gfs' not in paths


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
    assert '/gfs is http-only' in text
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
