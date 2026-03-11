from __future__ import annotations

from quart import jsonify

from server.gfs.tiles import tile_payload


def register_gfs_routes(app) -> None:
    @app.get('/api/gfs/tile/<int:z>/<int:x>/<int:y>')
    async def api_gfs_tile(z: int, x: int, y: int):
        return jsonify(tile_payload(z, x, y))
