from __future__ import annotations

import logging
from pathlib import Path

from quart import Quart

from server.ai.gemini import provider_name
from server.config import load_settings
from server.routes import register_routes
from server.rtc import RTCManager
from server.state import AppState


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"


def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def create_quart_app() -> Quart:
    settings = load_settings()
    _configure_logging(settings.debug)
    provider_name()

    app = Quart(
        __name__,
        static_folder=str(STATIC_DIR),
        static_url_path="/static",
    )
    state = AppState(default_room=settings.default_room)
    rtc = RTCManager(state)

    register_routes(app, state, settings, rtc)

    app.state_obj = state
    app.settings_obj = settings
    app.rtc_manager = rtc

    return app


def create_asgi_app():
    return create_quart_app()


def create_app():
    """Factory alias for process managers expecting create_app."""
    return create_quart_app()
