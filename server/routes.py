from __future__ import annotations

from quart import Quart, jsonify, request, send_from_directory

from server import ai
from server.config import Settings
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
    servers = [{"urls": "stun:stun.l.google.com:19302"}]

    turn_urls = []
    if settings.turn_url:
        turn_urls.append(_normalize_ice_url(settings.turn_url, "turn"))
    if settings.turns_url:
        turn_urls.append(_normalize_ice_url(settings.turns_url, "turns"))

    for raw in settings.turn_urls.split(","):
        raw = raw.strip()
        if not raw:
            continue
        turn_urls.append(_normalize_ice_url(raw, "turn"))

    if not turn_urls and settings.public_ip:
        turn_urls.extend(
            [
                f"turn:{settings.public_ip}:3478?transport=udp",
                f"turn:{settings.public_ip}:3478?transport=tcp",
            ]
        )

    if not turn_urls and settings.domain:
        turn_urls.extend(
            [
                f"turn:{settings.domain}:3478?transport=udp",
                f"turn:{settings.domain}:3478?transport=tcp",
                f"turns:{settings.domain}:5349",
            ]
        )

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
        return await send_from_directory(app.static_folder, "watch.html")

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
