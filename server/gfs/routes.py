from __future__ import annotations

from urllib.parse import quote

from quart import jsonify, request, send_file

from server.gfs_service import GFSService


def _bbox_from_request(default):
    south = float(request.args.get("south", default["south"]))
    west = float(request.args.get("west", default["west"]))
    north = float(request.args.get("north", default["north"]))
    east = float(request.args.get("east", default["east"]))
    return {"south": south, "west": west, "north": north, "east": east}


def register_gfs_routes(app, gfs: GFSService, static_dir) -> None:
    @app.get("/gfs")
    @app.get("/gfs/")
    async def gfs_page():
        return await send_file(str(static_dir / "indexgfs.html"))

    @app.get("/gfs/api/health")
    async def gfs_health():
        return jsonify(gfs.health_payload())

    @app.get("/gfs/api/config")
    async def gfs_config():
        return jsonify(gfs.config_payload())

    @app.get("/api/gfs")
    @app.get("/api/gfs/scene")
    @app.get("/gfs/api/scene")
    async def gfs_scene():
        return jsonify(gfs.get_scene_payload())

    @app.get('/api/gfs/status')
    async def api_gfs_status():
        return jsonify(gfs.status_payload())

    @app.get('/api/gfs/cloud-tiles')
    async def api_gfs_cloud_tiles():
        return jsonify(gfs.cloud_tiles_payload())

    @app.get('/api/gfs/hazards')
    async def api_gfs_hazards():
        return jsonify(gfs.hazards_payload())

    @app.get('/api/gfs/diagnostics')
    async def api_gfs_diagnostics():
        return jsonify(gfs.diagnostics_payload())

    @app.get("/gfs/api/fish")
    @app.get("/gfs/api/points")
    async def gfs_fish():
        return jsonify(gfs.fish_payload())

    @app.get("/gfs/api/clouds")
    @app.get("/gfs/api/cloud_tiles")
    async def gfs_clouds():
        return jsonify(gfs.cloud_tiles_payload())

    @app.get("/gfs/tile/<string:layer>/<int:z>/<int:x>/<int:y>")
    @app.get("/gfs/api/tile/<string:layer>/<int:z>/<int:x>/<int:y>")
    async def gfs_tile_layer(layer: str, z: int, x: int, y: int):
        debug = request.args.get("debug") == "1"
        return jsonify(gfs.tile_layer_payload(layer=layer, z=z, x=x, y=y, debug=debug))

    @app.get("/gfs/tile")
    async def gfs_tile_aggregate():
        z = int(request.args.get("z", 4))
        x = int(request.args.get("x", 4))
        y = int(request.args.get("y", 6))
        debug = request.args.get("debug") == "1"
        return jsonify(gfs.tile_aggregate_payload(z=z, x=x, y=y, debug=debug))

    @app.get("/gfs/api/tile/diagnostics")
    async def gfs_tile_diagnostics():
        layer = (request.args.get("layer") or "").strip().lower() or None
        tile = (request.args.get("tile") or "").strip() or None
        return jsonify(gfs.tile_diagnostics_payload(layer=layer, tile=tile))

    @app.get("/gfs/api/location_media")
    async def gfs_location_media():
        payload = gfs.location_media((request.args.get("location_key") or "").strip())
        return jsonify(payload), (200 if payload.get("ok") else 400)

    @app.post("/gfs/api/report/upsert")
    async def gfs_report_upsert():
        payload = await request.get_json(force=True)
        result = gfs.upsert_report((payload or {}).get("location_key") or "", (payload or {}).get("report_text") or "")
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.post("/gfs/api/live/upsert")
    async def gfs_live_upsert():
        payload = await request.get_json(force=True)
        result = gfs.upsert_live((payload or {}).get("location_key") or "", bool((payload or {}).get("active")), (payload or {}).get("stream_url") or "")
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.post("/gfs/api/live/start")
    async def gfs_live_start():
        payload = await request.get_json(force=True)
        location_key = ((payload or {}).get("location_key") or "").strip()
        if not location_key:
            return jsonify({"ok": False, "error": "missing location_key"}), 400
        result = gfs.upsert_live(location_key, True, f"/watch?room={quote(location_key)}")
        return jsonify(result), (200 if result.get("ok") else 400)

    @app.post("/gfs/api/live/stop")
    async def gfs_live_stop():
        payload = await request.get_json(force=True)
        location_key = ((payload or {}).get("location_key") or "").strip()
        if not location_key:
            return jsonify({"ok": False, "error": "missing location_key"}), 400
        result = gfs.upsert_live(location_key, False, "")
        return jsonify(result), (200 if result.get("ok") else 400)

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
        data = await file_obj.read()
        result = gfs.save_upload_video(location_key=location_key, filename=file_obj.filename or "upload.mp4", raw=data)
        return jsonify(result), (200 if result.get("ok") else 400)

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
