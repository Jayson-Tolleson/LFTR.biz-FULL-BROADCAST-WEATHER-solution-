from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from server.ai.gemini import provider_name
from server.config import load_settings
from server.gfs_service import GFSService
from server.rtc import RTCManager
from server.state import AppState
from server.routes_api.health import router as health_router
from server.routes_api.pages import router as pages_router
from server.routes_api.ai import router as ai_router
from server.routes_api.broadcast import router as broadcast_router
from server.routes_api.gfs import router as gfs_router, compat as gfs_compat_router
from server.routes_api.uploads import router as uploads_router
from server.ws.watch import init_ws_routes


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = STATIC_DIR


def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s - %(message)s")


def _validate_layout() -> None:
    if not STATIC_DIR.exists():
        raise RuntimeError(f"static directory missing at startup: {STATIC_DIR}")
    if not STATIC_DIR.is_dir():
        raise RuntimeError(f"static path is not a directory at startup: {STATIC_DIR}")
    if not TEMPLATES_DIR.exists() or not TEMPLATES_DIR.is_dir():
        raise RuntimeError(f"templates directory missing at startup: {TEMPLATES_DIR}")


def create_quart_app() -> FastAPI:
    settings = load_settings()
    _configure_logging(settings.debug)
    _validate_layout()
    provider_name()

    app = FastAPI(title="LFTR Broadcast + GFS", version="1.0")
    state = AppState(default_room=settings.default_room)
    rtc = RTCManager(state)
    gfs = GFSService(str(STATIC_DIR))

    app.state.settings = settings
    app.state.app_state = state
    app.state.rtc = rtc
    app.state.gfs = gfs

    pages_router.static_dir = STATIC_DIR

    app.include_router(health_router)
    app.include_router(pages_router)
    app.include_router(ai_router, prefix="/ai")
    app.include_router(broadcast_router)
    app.include_router(gfs_router)
    app.include_router(gfs_compat_router)
    app.include_router(uploads_router)

    # Backward compatible AI status endpoint.
    @app.get('/ai_status')
    async def ai_status_compat():
        from server.routes_api.ai import ai_status

        return await ai_status()


    @app.get('/api/gfs')
    async def api_gfs_legacy_scene():
        return gfs.get_scene_payload()

    app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')
    init_ws_routes(app, state, rtc)

    logging.getLogger("server.startup").info(
        "startup ready static=%s templates=%s routes=health,broadcast,watch,gfs,ai,uploads ws=watch/broadcast/chat",
        STATIC_DIR,
        TEMPLATES_DIR,
    )
    return app


def create_asgi_app():
    return create_quart_app()


def create_app():
    return create_quart_app()
