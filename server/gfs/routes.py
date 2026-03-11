from __future__ import annotations

from pathlib import Path
import logging

from quart import Blueprint, current_app, jsonify, request, send_file

from server.gfs.engine import GfsEngine
from server.gfs.errors import GfsError, InvalidBBoxError, NotFoundError
from server.gfs.live import build_live_session_payload
from server.gfs.locations import load_fish_locations, location_to_json
from server.gfs.media import LocationMediaStore


log = logging.getLogger("server.gfs.routes")


def create_gfs_blueprint(static_dir: Path) -> Blueprint:
    bp = Blueprint("gfs", __name__)

    def engine() -> GfsEngine:
        return current_app.extensions["gfs_engine"]

    def media_store() -> LocationMediaStore:
        return current_app.extensions["gfs_media_store"]

    def locations():
        return load_fish_locations(static_dir / "data" / "fishloclist.csv")

    def _location_or_404(location_id: str) -> dict:
        for loc in locations():
            payload = location_to_json(loc)
            if payload["id"] == location_id:
                return payload
        raise NotFoundError(f"location '{location_id}' not found", provider="gfs_locations")

    @bp.get("/gfs")
    @bp.get("/gfs/")
    async def gfs_page():
        return await send_file(str(static_dir / "indexgfs.html"))

    @bp.get("/gfs/api/config")
    async def gfs_config():
        settings = getattr(current_app, "settings_obj", None)
        return jsonify({
            "google_maps_api_key": getattr(settings, "google_maps_api_key", ""),
            "debug": engine().config.debug_enabled,
        })

    @bp.get("/gfs/api/locations")
    async def gfs_locations():
        return jsonify({"locations": [location_to_json(loc) for loc in locations()]})

    @bp.get("/gfs/api/location/<string:location_id>")
    async def gfs_location(location_id: str):
        try:
            loc = _location_or_404(location_id)
            loc["reports"] = loc.pop("all_reports")
            loc["videos"] = [v.__dict__ for v in media_store().videos_for(location_id)]
            loc["live"] = media_store().live_for(location_id)
            return jsonify(loc)
        except GfsError as exc:
            log.error("location route failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/location/<string:location_id>/reports")
    async def gfs_location_reports(location_id: str):
        try:
            loc = _location_or_404(location_id)
            reports = list(loc["all_reports"]) + media_store().reports_for(location_id)
            return jsonify({"location_id": location_id, "reports": reports})
        except GfsError as exc:
            log.error("reports route failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.post("/gfs/api/location/<string:location_id>/reports")
    async def gfs_location_report_upsert(location_id: str):
        try:
            _location_or_404(location_id)
            payload = await request.get_json(force=True)
            text = str((payload or {}).get("report") or "").strip()
            if not text:
                raise InvalidBBoxError("report is required")
            media_store().append_report(location_id, text)
            return jsonify({"ok": True, "location_id": location_id, "report": text})
        except GfsError as exc:
            log.error("report upsert failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/location/<string:location_id>/videos")
    async def gfs_location_videos(location_id: str):
        try:
            _location_or_404(location_id)
            videos = [v.__dict__ for v in media_store().videos_for(location_id)]
            return jsonify({"location_id": location_id, "videos": videos})
        except GfsError as exc:
            log.error("videos route failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.post("/gfs/api/location/<string:location_id>/upload")
    async def gfs_location_upload(location_id: str):
        try:
            _location_or_404(location_id)
            files = await request.files
            file_obj = files.get("file")
            if not file_obj:
                raise InvalidBBoxError("missing file")
            data = await file_obj.read()
            saved = media_store().save_upload(location_id=location_id, filename=file_obj.filename or "upload.mp4", raw=data)
            return jsonify({"ok": True, "location_id": location_id, **saved})
        except GfsError as exc:
            log.error("upload route failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/location/<string:location_id>/live")
    async def gfs_location_live_get(location_id: str):
        try:
            _location_or_404(location_id)
            return jsonify({"location_id": location_id, "live": media_store().live_for(location_id)})
        except GfsError as exc:
            log.error("live GET failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.post("/gfs/api/location/<string:location_id>/live")
    async def gfs_location_live_post(location_id: str):
        try:
            _location_or_404(location_id)
            payload = await request.get_json(force=True)
            active = bool((payload or {}).get("active"))
            stream_url = str((payload or {}).get("stream_url") or "")
            live = media_store().set_live(location_id, active=active, stream_url=stream_url)
            event_type = "snapshot_changed" if active else "provider_recovered"
            await engine().broadcast_control(event_type, {"location_id": location_id, "active": active})
            return jsonify({"ok": True, "location_id": location_id, "live": live, "session": build_live_session_payload(location_id)})
        except GfsError as exc:
            log.error("live POST failed path=%s location_id=%s err=%s", request.path, location_id, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/weather")
    async def gfs_weather():
        try:
            intent = engine().parse_intent(request.args)
            payload = await engine().weather_payload(intent)
            return jsonify(payload)
        except GfsError as exc:
            log.error("weather route failed path=%s args=%s err=%s", request.path, dict(request.args), exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/clouds")
    async def gfs_clouds():
        try:
            intent = engine().parse_intent(request.args)
            return jsonify(await engine().clouds_payload(intent))
        except GfsError as exc:
            log.error("clouds route failed path=%s args=%s err=%s", request.path, dict(request.args), exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/bait")
    async def gfs_bait():
        try:
            intent = engine().parse_intent(request.args)
            return jsonify(await engine().bait_payload(intent))
        except GfsError as exc:
            log.error("bait route failed path=%s args=%s err=%s", request.path, dict(request.args), exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.get("/gfs/api/health")
    async def gfs_health():
        payload = engine().health_payload()
        payload["locations"] = {"count": len(locations())}
        return jsonify(payload)

    @bp.get("/gfs/api/debug")
    async def gfs_debug():
        if not engine().config.debug_enabled:
            return jsonify({"error": "notfound", "message": "debug endpoint disabled", "provider": "gfs", "retryable": False}), 404
        return jsonify({"snapshot": engine().snapshot.__dict__, "cache": engine().cache.stats()})

    @bp.get("/gfs/api/location_media")
    async def gfs_legacy_location_media():
        location_id = (request.args.get("location_key") or "").strip()
        return await gfs_location(location_id)

    @bp.post("/gfs/api/report/upsert")
    async def gfs_legacy_report_upsert():
        try:
            payload = await request.get_json(force=True)
            location_id = str((payload or {}).get("location_key") or "").strip()
            _location_or_404(location_id)
            text = str((payload or {}).get("report_text") or "").strip()
            if not text:
                raise InvalidBBoxError("report is required")
            media_store().append_report(location_id, text)
            return jsonify({"ok": True, "location_id": location_id, "report": text})
        except GfsError as exc:
            log.error("legacy report upsert failed path=%s err=%s", request.path, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.post("/gfs/api/live/upsert")
    async def gfs_legacy_live_upsert():
        try:
            payload = await request.get_json(force=True)
            location_id = str((payload or {}).get("location_key") or "").strip()
            _location_or_404(location_id)
            active = bool((payload or {}).get("active"))
            stream_url = str((payload or {}).get("stream_url") or "")
            live = media_store().set_live(location_id, active=active, stream_url=stream_url)
            await engine().broadcast_control("snapshot_changed", {"location_id": location_id, "active": active})
            return jsonify({"ok": True, "location_id": location_id, "live": live, "session": build_live_session_payload(location_id)})
        except GfsError as exc:
            log.error("legacy live upsert failed path=%s err=%s", request.path, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.post("/gfs/api/upload_video")
    async def gfs_legacy_upload_video():
        try:
            files = await request.files
            form = await request.form
            location_id = (form.get("location_key") or "").strip()
            if not files.get("file"):
                raise InvalidBBoxError("missing file")
            return await gfs_location_upload(location_id)
        except GfsError as exc:
            log.error("legacy upload_video failed path=%s err=%s", request.path, exc, exc_info=True)
            return jsonify(exc.to_json()), exc.status_code

    @bp.websocket("/ws/gfs")
    async def ws_gfs():
        await engine().websocket_handler()

    return bp
