from __future__ import annotations

from quart import jsonify, websocket

from server.gfs.tiles import tile_payload
from server.gfs.websocket import hub


def register_gfs_routes(app) -> None:
    @app.websocket('/ws/gfs')
    async def ws_gfs():
        ws = websocket._get_current_object()
        hub.clients.add(ws)
        try:
            while True:
                payload = await websocket.receive_json()
                await hub.broadcast(payload)
        finally:
            hub.clients.discard(ws)

    @app.get('/api/gfs/tile/<int:z>/<int:x>/<int:y>')
    async def api_gfs_tile(z: int, x: int, y: int):
        return jsonify(tile_payload(z, x, y))
