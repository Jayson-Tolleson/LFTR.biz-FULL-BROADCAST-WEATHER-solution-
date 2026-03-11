from __future__ import annotations

from pathlib import Path

from quart import Quart, jsonify, request, send_file

from server import ai
from server.ai import ai_status as get_ai_status
from server.ai.gemini import provider_name
from server.config import Settings


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _static_file(path_name: str, static_dir: Path | None = None) -> Path:
    root = static_dir or STATIC_DIR
    if not root.exists():
        raise FileNotFoundError(f"static directory missing: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"static path is not a directory: {root}")
    path = root / path_name
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"static file missing: {path}")
    return path


def _static_error_response(app: Quart, static_dir: Path | None = None):
    app.logger.error("[routes] static deployment invalid at %s", static_dir or STATIC_DIR)
    return jsonify({"ok": False, "error": "static deployment invalid"}), 500


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
    raw_urls: list[str] = []
    if settings.turn_url:
        raw_urls.append(_normalize_ice_url(settings.turn_url, "turn"))
    if settings.turns_url:
        raw_urls.append(_normalize_ice_url(settings.turns_url, "turns"))
    for raw in settings.turn_urls.split(","):
        if raw.strip():
            raw_urls.append(_normalize_ice_url(raw.strip(), "turn"))
    host = settings.domain or settings.public_ip
    if host:
        raw_urls.extend([
            f"turn:{host}:3478?transport=udp",
            f"turn:{host}:3478?transport=tcp",
            f"turns:{host}:5349",
        ])
    turn_urls = []
    seen = set()
    for u in raw_urls:
        if not u or u in seen:
            continue
        seen.add(u)
        turn_urls.append(u)
    if turn_urls and settings.turn_username and settings.turn_password:
        servers.append({"urls": turn_urls, "username": settings.turn_username, "credential": settings.turn_password})
    return servers


def register_core_routes(app: Quart, settings: Settings, static_dir: Path | None = None) -> None:
    @app.get("/")
    async def index():
        try:
            return await send_file(str(_static_file("index.html", static_dir)))
        except Exception:
            return _static_error_response(app, static_dir)

    @app.get("/health")
    async def health():
        return jsonify({"ok": True})

    @app.get("/broadcast")
    async def broadcast_page():
        try:
            return await send_file(str(_static_file("broadcast.html", static_dir)))
        except Exception:
            return _static_error_response(app, static_dir)

    @app.get("/watch")
    async def watch_page():
        try:
            return await send_file(str(_static_file("watch.html", static_dir)))
        except Exception:
            return _static_error_response(app, static_dir)

    @app.get("/favicon.ico")
    async def favicon():
        try:
            return await send_file(str(_static_file("favicon.ico", static_dir)))
        except Exception:
            return "", 204

    @app.get("/status")
    @app.get("/status-dashboard")
    async def status_dashboard():
        try:
            return await send_file(str(_static_file("status_dashboard.html", static_dir)))
        except Exception:
            return _static_error_response(app, static_dir)

    @app.get("/webrtc/ice-config")
    async def webrtc_ice_config():
        return jsonify({"iceServers": build_ice_servers(settings)})

    @app.get("/ai_status")
    async def ai_status():
        ai_provider = provider_name()
        auth = get_ai_status()
        ai_available = bool(settings.ai_enabled and ai_provider != "stub")
        return jsonify({
            "ai_available": ai_available,
            "ai_enabled": settings.ai_enabled,
            "provider": ai_provider,
            "tts_available": ai_available,
            "stt_available": bool(auth.get("stt_ready")),
            "google_auth": auth,
        })

    @app.post("/ai/chat")
    async def ai_chat():
        payload = await request.get_json(force=True)
        return jsonify(await ai.handle_chat(payload, settings.ai_fallback_text))

    @app.post("/ai/tts")
    async def ai_tts():
        payload = await request.get_json(force=True)
        return await ai.handle_tts(payload)

    @app.post("/ai/websearch")
    async def ai_websearch():
        payload = await request.get_json(force=True)
        return jsonify(await ai.handle_websearch(payload))
