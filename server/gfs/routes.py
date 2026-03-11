from __future__ import annotations

from pathlib import Path

from quart import Blueprint, current_app, jsonify, request, send_file

from server.gfs.engine import GfsEngine
from server.gfs.errors import GfsError


def create_gfs_blueprint(static_dir: Path) -> Blueprint:
    bp = Blueprint("gfs", __name__)

    def engine() -> GfsEngine:
        return current_app.extensions["gfs_engine"]

    @bp.get("/gfs")
    @bp.get("/gfs/")
    async def gfs_page():
        return await send_file(str(static_dir / "indexgfs.html"))

    @bp.get("/gfs/api/weather")
    async def gfs_weather():
        try:
            intent = engine().parse_intent(request.args)
            payload = await engine().weather_payload(intent)
            if engine().snapshot.payloads is not None:
                engine().snapshot.payloads["weather"] = payload
            return jsonify(payload)
        except GfsError as exc:
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/clouds")
    async def gfs_clouds():
        try:
            intent = engine().parse_intent(request.args)
            return jsonify(await engine().clouds_payload(intent))
        except GfsError as exc:
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/bait")
    async def gfs_bait():
        try:
            intent = engine().parse_intent(request.args)
            return jsonify(await engine().bait_payload(intent))
        except GfsError as exc:
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/health")
    async def gfs_health():
        return jsonify(engine().health_payload())

    @bp.get("/gfs/api/debug")
    async def gfs_debug():
        if not engine().config.debug_enabled:
            return jsonify({"error": "notfound", "message": "debug endpoint disabled", "provider": "gfs", "retryable": False}), 404
        return jsonify({"snapshot": engine().snapshot.__dict__, "cache": engine().cache.stats()})

    @bp.websocket("/ws/gfs")
    async def ws_gfs():
        await engine().websocket_handler()

    return bp
