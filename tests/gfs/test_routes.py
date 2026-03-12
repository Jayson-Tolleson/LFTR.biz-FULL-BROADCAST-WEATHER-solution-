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


def test_health_route_and_locations():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get("/gfs/api/health")
        assert res.status_code == 200
        payload = await res.get_json()
        assert "providers" in payload
        assert "cache" in payload

        loc_res = await client.get("/gfs/api/locations")
        assert loc_res.status_code == 200
        loc_payload = await loc_res.get_json()
        assert isinstance(loc_payload.get("locations"), list)
        assert loc_payload["locations"]

    asyncio.run(_run())


def test_location_reports_and_videos_flow(tmp_path, monkeypatch):
    async def _run():
        app = create_app()
        client = app.test_client()
        locs = await (await client.get("/gfs/api/locations")).get_json()
        loc_id = locs["locations"][0]["id"]

        save_report = await client.post(f"/gfs/api/location/{loc_id}/reports", json={"report": "fresh bite window"})
        assert save_report.status_code == 200

        reports = await client.get(f"/gfs/api/location/{loc_id}/reports")
        r_payload = await reports.get_json()
        assert any("fresh bite" in item for item in r_payload["reports"])

        upload_res = await client.post(f"/gfs/api/location/{loc_id}/upload")
        assert upload_res.status_code == 400

        saved = app.extensions["gfs_media_store"].save_upload(location_id=loc_id, filename="clip.webm", raw=b"fake-video")
        assert saved["url"].startswith("/static/fishvid/")

        videos = await client.get(f"/gfs/api/location/{loc_id}/videos")
        v_payload = await videos.get_json()
        assert isinstance(v_payload["videos"], list)

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


def test_bait_route_serialization_is_compatible():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get('/gfs/api/bait?bbox=-140,20,-100,50&quality=coarse')
        if res.status_code == 503:
            payload = await res.get_json()
            assert payload['error'] == 'providerunavailable'
            return
        assert res.status_code == 200
        payload = await res.get_json()
        assert 'front_lines' in payload

    asyncio.run(_run())


def test_missing_location_live_returns_clean_404():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get('/gfs/api/location/does-not-exist/live')
        assert res.status_code == 404
        payload = await res.get_json()
        assert payload['error'] == 'location_not_found'

    asyncio.run(_run())


def test_bait_advanced_route_available():
    async def _run():
        app = create_app()
        client = app.test_client()
        res = await client.get('/gfs/api/bait/advanced?bbox=-140,20,-100,50&quality=coarse')
        if res.status_code == 503:
            payload = await res.get_json()
            assert payload['error'] == 'providerunavailable'
            return
        assert res.status_code == 200
        payload = await res.get_json()
        assert 'bait_advanced_field_v1' in payload

    asyncio.run(_run())
