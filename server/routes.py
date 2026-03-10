from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from quart import Quart, jsonify, request, send_file, websocket

from server import ai
from server.ai.gemini import provider_name
from server.api import api_bp
from server.config import Settings
from server.gfs_service import GFSService
from server.rtc import RTCManager
from server.state import AppState
from server.weather_tiles.gfs_tiles import tile_to_bounds, marching_squares_precip
from server.cloud_engine.cloud_builder import build_cloud_clusters
from server.broadcast.routes import register_broadcast_routes
from server.gfs.routes import register_gfs_routes


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _normalize_ice_url(raw: str, default_scheme: str = "turn") -> str:
    val = (raw or "").strip()
    if not val:
        return ""
    if val.startswith(("stun:", "turn:", "turns:")):
        return val

    host = val
    port = "3478"
    if ":" in val and "?" not in val:
        maybe_host, maybe_port = val.rsplit(":", 1)
        if maybe_port.isdigit():
            host, port = maybe_host, maybe_port

    if default_scheme == "turns":
        if port == "3478":
            port = "5349"
        return f"turns:{host}:{port}"

    return f"turn:{host}:{port}?transport=udp"


def build_ice_servers(settings: Settings):
    servers = [{"urls": ["stun:stun.l.google.com:19302"]}]

    raw_urls = []
    if settings.turn_url:
        raw_urls.append(_normalize_ice_url(settings.turn_url, "turn"))
    if settings.turns_url:
        raw_urls.append(_normalize_ice_url(settings.turns_url, "turns"))

    for raw in settings.turn_urls.split(","):
        raw = raw.strip()
        if raw:
            raw_urls.append(_normalize_ice_url(raw, "turn"))

    host = settings.domain or settings.public_ip
    if host:
        raw_urls.extend([
            f"turn:{host}:3478?transport=udp",
            f"turn:{host}:3478?transport=tcp",
            f"turns:{host}:5349",
        ])

    turn_urls = []
    seen = set()
    for url in raw_urls:
        u = (url or "").strip()
        if not u or u in seen:
            continue
        seen.add(u)
        turn_urls.append(u)

    if turn_urls and settings.turn_username and settings.turn_password:
        servers.append(
            {
                "urls": turn_urls,
                "username": settings.turn_username,
                "credential": settings.turn_password,
            }
        )

    return servers


def register_routes(app: Quart, state: AppState, settings: Settings, rtc: RTCManager) -> None:
    @app.get("/")
    async def index():
        return await send_file(str(STATIC_DIR / "index.html"))

    @app.get("/health")
    async def health():
        return jsonify({"ok": True}), 200

    @app.get("/broadcast")
    async def broadcast():
        return await send_file(str(STATIC_DIR / "broadcast.html"))

    @app.get("/watch")
    async def watch():
        return await send_file(str(STATIC_DIR / "watch.html"))

    @app.get("/status-dashboard")
    async def status_dashboard():
        return await send_file(str(STATIC_DIR / "status_dashboard.html"))

    @app.get("/webrtc/ice-config")
    async def webrtc_ice_config():
        return jsonify({"iceServers": build_ice_servers(settings)})

    @app.get("/ai_status")
    async def ai_status():
        ai_provider = provider_name()
        ai_available = bool(settings.ai_enabled and ai_provider != "stub")
        return jsonify({"ai_available": ai_available, "ai_enabled": settings.ai_enabled, "provider": ai_provider, "tts_available": ai_provider})

    @app.post("/ai/chat")
    async def ai_chat():
        payload = await request.get_json(force=True)
        data = await ai.handle_chat(payload, settings.ai_fallback_text)
        return jsonify(data)

    @app.post("/ai/tts")
    async def ai_tts():
        payload = await request.get_json(force=True)
        return await ai.handle_tts(payload)

    @app.post("/ai/websearch")
    async def ai_websearch():
        payload = await request.get_json(force=True)
        return jsonify(await ai.handle_websearch(payload))

    gfs = GFSService(str(STATIC_DIR))

    register_broadcast_routes(app)
    register_gfs_routes(app)
    app.register_blueprint(api_bp)

    @app.get("/gfs")
    @app.get("/gfs/")
    async def gfs_page():
        return await send_file(str(STATIC_DIR / "indexgfs.html"))

    @app.get("/gfs/api/health")
    async def gfs_health():
        payload = gfs.health()
        payload["maps3d_available"] = bool(settings.google_maps_api_key)
        return jsonify(payload)

    @app.get("/gfs/api/config")
    async def gfs_config():
        payload = gfs.config()
        payload["google_maps_api_key"] = settings.google_maps_api_key
        payload["mapsApiKey"] = settings.google_maps_api_key
        payload["maps3d_available"] = bool(settings.google_maps_api_key)
        return jsonify(payload)


    @app.get("/api/gfs")
    async def api_gfs_scene_proxy():
        payload = gfs.cloud_tiles_payload()
        return jsonify(payload)

    @app.get("/gfs/api/fish")
    @app.get("/gfs/api/points")
    async def gfs_fish():
        payload = gfs.fish_payload()
        status = 200 if payload.get("ok") else 500
        return jsonify(payload), status


    @app.get("/gfs/api/clouds")
    @app.get("/gfs/api/cloud_tiles")
    @app.get("/gfs/api/scene")
    async def gfs_cloud_tiles():
        def _q(name: str, default: float) -> float:
            try:
                return float(request.args.get(name, default))
            except Exception:
                return default

        def _qi(name: str, default: int = 0) -> int:
            try:
                return int(request.args.get(name, default))
            except Exception:
                return default

        def _compact_tile(tile: dict) -> dict:
            out = dict(tile)
            out.pop("subcells", None)
            bands = out.get("bands") if isinstance(out.get("bands"), dict) else {}
            compact_bands = {}
            for band_name in ("low", "mid", "high"):
                band = bands.get(band_name) if isinstance(bands.get(band_name), dict) else {}
                compact_bands[band_name] = {
                    "density": band.get("density"),
                    "coverage": band.get("coverage"),
                    "base_altitude_m": band.get("base_altitude_m"),
                    "top_altitude_m": band.get("top_altitude_m"),
                    "thickness_m": band.get("thickness_m"),
                    "lateral_scale_km": band.get("lateral_scale_km"),
                    "wind": band.get("wind") or {"u": 0, "v": 0},
                }
            out["bands"] = compact_bands
            return out

        bbox = {
            "west": _q("west", -180.0),
            "south": _q("south", -80.0),
            "east": _q("east", 180.0),
            "north": _q("north", 80.0),
        }
        limit = max(0, _qi("limit", 0))
        compact = (request.args.get("compact", "0") or "0").strip().lower() in {"1", "true", "yes", "on"}

        payload = gfs.cloud_tiles_payload(bbox)
        items = payload.get("items") or []
        if limit > 0 and isinstance(items, list):
            items = sorted(items, key=lambda t: float((t or {}).get("importance", 0.0)), reverse=True)[:limit]
        if compact and isinstance(items, list):
            items = [_compact_tile(t) for t in items]
        payload["items"] = items
        regime_counts = {}
        convective_tile_count = 0
        deck_tile_count = 0
        cirrus_tile_count = 0
        for item in items:
            regime = (item.get("regime") or "unknown").strip() or "unknown"
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            if regime == "deep_convection":
                convective_tile_count += 1
            if regime in {"marine_stratocumulus", "frontal_shield"}:
                deck_tile_count += 1
            if regime == "cirrus_sheet":
                cirrus_tile_count += 1

        summary = payload.get("summary")
        if isinstance(summary, dict):
            summary.update(
                {
                    "regime_counts": regime_counts,
                    "convective_tile_count": convective_tile_count,
                    "deck_tile_count": deck_tile_count,
                    "cirrus_tile_count": cirrus_tile_count,
                }
            )
        else:
            payload["summary"] = {
                "regime_counts": regime_counts,
                "convective_tile_count": convective_tile_count,
                "deck_tile_count": deck_tile_count,
                "cirrus_tile_count": cirrus_tile_count,
            }

        # Keep new scene schema synchronized with route-level limit/compact transforms.
        scene = payload.get("scene") if isinstance(payload.get("scene"), dict) else {}
        if scene:
            scene["clouds"] = items
            payload["scene"] = scene
        status_obj = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        if status_obj:
            status_obj["request_bounds"] = bbox
            payload["status"] = status_obj

        return jsonify(payload)


    @app.get("/gfs/tile/<string:layer>/<int:z>/<int:x>/<int:y>")
    @app.get("/gfs/api/tile/<string:layer>/<int:z>/<int:x>/<int:y>")
    async def gfs_layer_tile(layer: str, z: int, x: int, y: int):
        try:
            pad = float(request.args.get("pad", 0.18))
        except Exception:
            pad = 0.18
        debug = str(request.args.get("debug", "0")).strip().lower() in {"1", "true", "yes", "on"}
        payload = gfs.layer_tile_payload(layer, z, x, y, pad_deg=max(0.0, min(1.2, pad)), debug=debug)
        status = 200 if (payload.get("status") or {}).get("ok", True) else 400
        return jsonify(payload), status


    @app.get("/gfs/tile")
    async def gfs_tile_aggregate():
        try:
            z = int(request.args.get("z", 2))
            x = int(request.args.get("x", 0))
            y = int(request.args.get("y", 0))
        except Exception:
            z, x, y = 2, 0, 0

        bounds = tile_to_bounds(z, x, y)
        clouds = []
        precip = []
        wind_vectors = []
        try:
            sf = gfs.state.scalar_fields or {}
            cloud = sf.get("cloud_density") or {}
            hum = sf.get("humidity") or sf.get("cloud_density") or {}
            pr = sf.get("precip_rate") or {}
            wu = sf.get("wind_u") or sf.get("wind_speed") or {}
            wv = sf.get("wind_v") or sf.get("wind_speed") or {}
            lat = cloud.get("lat") or pr.get("lat")
            lon = cloud.get("lon") or pr.get("lon")
            if lat is not None and lon is not None:
                import numpy as np
                lat_a = np.asarray(lat, dtype=float)
                lon_a = np.asarray(lon, dtype=float)
                cloud_a = np.asarray(cloud.get("values"), dtype=float) if cloud.get("values") is not None else None
                hum_a = np.asarray(hum.get("values"), dtype=float) if hum.get("values") is not None else None
                pr_a = np.asarray(pr.get("values"), dtype=float) if pr.get("values") is not None else None
                if cloud_a is not None:
                    clouds = build_cloud_clusters(lat_a, lon_a, cloud_a, hum_a, z)[:260]
                if pr_a is not None:
                    precip = marching_squares_precip(lat_a, lon_a, pr_a, z)[:320]
                if wu.get("values") is not None and wv.get("values") is not None:
                    u = np.asarray(wu.get("values"), dtype=float)
                    v = np.asarray(wv.get("values"), dtype=float)
                    step = 10 if z < 4 else 6 if z < 7 else 4
                    for iy in range(0, min(u.shape[0], lat_a.shape[0]), step):
                        for ix in range(0, min(u.shape[1], lat_a.shape[1]), step):
                            wind_vectors.append({
                                "lat": float(lat_a[iy, ix]),
                                "lon": float(lon_a[iy, ix]),
                                "u": float(u[iy, ix]),
                                "v": float(v[iy, ix]),
                            })
                def in_bounds(item):
                    lat_v = float(item.get("lat", 0.0))
                    lon_v = float(item.get("lon", 0.0))
                    return bounds["south"] <= lat_v <= bounds["north"] and bounds["west"] <= lon_v <= bounds["east"]
                clouds = [c for c in clouds if in_bounds(c)]
                wind_vectors = [w for w in wind_vectors if in_bounds(w)]
        except Exception:
            clouds, precip, wind_vectors = [], [], []

        return jsonify({
            "z": z, "x": x, "y": y,
            "bounds": bounds,
            "clouds": clouds,
            "precipitation": precip,
            "wind_vectors": wind_vectors,
        })

    @app.get("/gfs/api/tile/diagnostics")
    async def gfs_tile_diagnostics():
        layer = (request.args.get("layer") or "").strip().lower() or None
        tile = (request.args.get("tile") or "").strip() or None
        return jsonify(gfs.tile_diagnostics_payload(layer=layer, tile=tile))

    @app.get("/gfs/api/location_media")
    async def gfs_location_media():
        location_key = (request.args.get("location_key") or "").strip()
        payload = gfs.location_media(location_key)
        status = 200 if payload.get("ok") else 400
        return jsonify(payload), status

    @app.post("/gfs/api/report/upsert")
    async def gfs_report_upsert():
        payload = await request.get_json(force=True)
        location_key = (payload or {}).get("location_key") or ""
        report_text = (payload or {}).get("report_text") or ""
        result = gfs.upsert_report(location_key, report_text)
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.post("/gfs/api/live/upsert")
    async def gfs_live_upsert():
        payload = await request.get_json(force=True)
        location_key = (payload or {}).get("location_key") or ""
        active = bool((payload or {}).get("active"))
        stream_url = (payload or {}).get("stream_url") or ""
        result = gfs.upsert_live(location_key, active, stream_url)
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.post("/gfs/api/live/start")
    async def gfs_live_start():
        payload = await request.get_json(force=True)
        location_key = ((payload or {}).get("location_key") or "").strip()
        if not location_key:
            return jsonify({"ok": False, "error": "missing location_key"}), 400
        stream_url = f"/watch?room={quote(location_key)}"
        result = gfs.upsert_live(location_key, True, stream_url)
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.post("/gfs/api/live/stop")
    async def gfs_live_stop():
        payload = await request.get_json(force=True)
        location_key = ((payload or {}).get("location_key") or "").strip()
        if not location_key:
            return jsonify({"ok": False, "error": "missing location_key"}), 400
        result = gfs.upsert_live(location_key, False, "")
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.post("/gfs/api/upload_video")
    async def gfs_upload_video():
        form = await request.form
        files = await request.files
        location_key = (form.get("location_key") or "").strip()
        file_obj = files.get("file")
        if not location_key:
            return jsonify({"ok": False, "error": "missing location_key"}), 400
        if not file_obj:
            return jsonify({"ok": False, "error": "missing file"}), 400

        data = await file_obj.read()
        result = gfs.save_upload_video(location_key=location_key, filename=file_obj.filename or "upload.mp4", raw=data)
        status = 200 if result.get("ok") else 400
        return jsonify(result), status

    @app.get("/gfs/api/frame")
    async def gfs_frame():
        return jsonify(gfs.frame_payload())

    @app.get("/gfs/api/overlay")
    async def gfs_overlay():
        return jsonify(gfs.overlay_payload())

    @app.get("/gfs/api/contours")
    async def gfs_contours():
        return jsonify(gfs.contours_payload())

    @app.get("/gfs/api/legend")
    async def gfs_legend():
        return jsonify(gfs.legend_payload())

    @app.get("/gfs/api/tiles/<int:z>/<int:x>/<int:y>")
    async def gfs_tiles(z: int, x: int, y: int):
        png = gfs.tile_png_bytes(z, x, y)
        return png, 200, {"Content-Type": "image/png", "Cache-Control": "no-store"}

    @app.websocket("/gfs/ws/updates")
    async def gfs_ws_updates():
        while True:
            payload = await gfs.ws_snapshot()
            await websocket.send_json(payload)
            await asyncio.sleep(5)
