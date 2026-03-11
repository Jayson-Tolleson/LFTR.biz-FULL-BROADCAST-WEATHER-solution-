from __future__ import annotations

import asyncio

from server.app_factory import create_app
from server.gfs.errors import ProviderUnavailableError


def test_weather_and_clouds_route_json_shape():
    async def _run():
        app = create_app()
        client = app.test_client()
        q = "bbox=-140,20,-100,50&quality=coarse"
        weather = await client.get(f"/gfs/api/weather?{q}")
        if weather.status_code == 503:
            payload = await weather.get_json()
            assert payload["error"] == "providerunavailable"
        else:
            payload = await weather.get_json()
            assert "fields" in payload
            assert "bbox" in payload

        clouds = await client.get(f"/gfs/api/clouds?{q}")
        if clouds.status_code == 503:
            payload = await clouds.get_json()
            assert payload["error"] == "providerunavailable"
        else:
            payload = await clouds.get_json()
            assert "cloud_layers" in payload

    asyncio.run(_run())


def test_health_route():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get("/gfs/api/health")
        assert res.status_code == 200
        payload = await res.get_json()
        assert "providers" in payload
        assert "cache" in payload

    asyncio.run(_run())


def test_provider_failure_returns_503(monkeypatch):
    async def _run():
        app = create_app()
        async def _fail(**kwargs):
            raise ProviderUnavailableError("upstream down", provider="thredds_gfs")
        app.extensions["gfs_engine"].atmospheric.fetch_subset = _fail

        client = app.test_client()
        res = await client.get("/gfs/api/weather?bbox=-140,20,-100,50")
        assert res.status_code == 503
        payload = await res.get_json()
        assert payload["provider"] == "thredds_gfs"

    asyncio.run(_run())


def test_websocket_connect_disconnect_lifecycle():
    async def _run():
        app = create_app()
        client = app.test_client()
        async with client.websocket('/ws/gfs') as ws:
            msg = await ws.receive_json()
            assert msg["type"] == "connected"
        assert len(app.extensions["gfs_engine"]._ws_clients) == 0

    asyncio.run(_run())
