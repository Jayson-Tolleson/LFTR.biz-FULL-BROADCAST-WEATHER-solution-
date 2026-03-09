from __future__ import annotations

from quart import Blueprint, jsonify

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.get("/health")
async def api_health():
    return jsonify({"ok": True, "service": "broadcast-weather"})
