from __future__ import annotations

import logging

from quart import Quart
import socketio

from server.config import load_settings
from server.routes import register_routes
from server.rtc import RTCManager
from server.socket_handlers import register_socket_handlers
from server.state import AppState



def _configure_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )



def create_asgi_app():
    settings = load_settings()
    _configure_logging(settings.debug)

    app = Quart(__name__, static_folder=settings.static_dir, static_url_path="/static")
    state = AppState(default_room=settings.default_room)
    rtc = RTCManager(state)

    sio = socketio.AsyncServer(
        async_mode="asgi",
        cors_allowed_origins="*",
        max_http_buffer_size=20000000,
        ping_interval=25,
        ping_timeout=60,
    )
    state.sio = sio

    register_routes(app, state, settings, rtc)
    register_socket_handlers(sio, state, settings, rtc)

    app.state_obj = state
    app.settings_obj = settings
    app.rtc_manager = rtc

    return socketio.ASGIApp(sio, other_asgi_app=app, socketio_path=settings.socket_path.lstrip("/"))
