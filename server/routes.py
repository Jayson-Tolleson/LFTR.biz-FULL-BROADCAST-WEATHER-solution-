from __future__ import annotations

import asyncio

from quart import Quart, jsonify, request, send_from_directory, websocket

from server import ai
from server.config import Settings
from server.gfs_service import GFSService
from server.rtc import RTCManager
from server.state import AppState


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
        return await send_from_directory(app.static_folder, "index.html")

    @app.get("/broadcast")
    async def broadcast():
        return await send_from_directory(app.static_folder, "broadcast.html")

    @app.get("/watch")
    async def watch():
        return await send_from_directory(app.static_folder, "watch.html")

    @app.get("/status-dashboard")
    async def status_dashboard():
        return await send_from_directory(app.static_folder, "status_dashboard.html")

    @app.get("/webrtc/ice-config")
    async def webrtc_ice_config():
        return jsonify({"iceServers": build_ice_servers(settings)})

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

    gfs = GFSService(app.static_folder)

    @app.get("/gfs")
    @app.get("/gfs/")
    async def gfs_page():
        return await send_from_directory(app.static_folder, "indexgfs.html")

    @app.get("/gfs/api/health")
    async def gfs_health():
        payload = gfs.health()
        payload["maps3d_available"] = bool(settings.google_maps_api_key)
        return jsonify(payload)

    @app.get("/gfs/api/config")
    async def gfs_config():
        payload = gfs.config()
        payload["google_maps_api_key"] = settings.google_maps_api_key
        payload["maps3d_available"] = bool(settings.google_maps_api_key)
        return jsonify(payload)

    @app.get("/gfs/api/fish")
    @app.get("/gfs/api/points")
    async def gfs_fish():
        payload = gfs.fish_payload()
        status = 200 if payload.get("ok") else 500
        return jsonify(payload), status

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

        data = file_obj.read()
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
