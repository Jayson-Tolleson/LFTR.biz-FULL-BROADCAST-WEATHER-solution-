from __future__ import annotations

from quart import Blueprint, current_app, jsonify, request

from server.gfs.errors import GfsError

api_bp = Blueprint("api", __name__, url_prefix="/api")

@api_bp.get("/health")
async def api_health():
    return jsonify({"ok": True, "service": "broadcast-weather"})


def _sample_grid(grid, lat: float, lon: float, bbox: list[float]):
    if not isinstance(grid, list) or not grid or not isinstance(grid[0], list):
        return None
    arr = grid[0] if isinstance(grid[0][0], list) else grid
    ny = len(arr)
    nx = len(arr[0]) if ny and isinstance(arr[0], list) else 0
    if ny < 1 or nx < 1:
        return None
    west, south, east, north = [float(v) for v in bbox]
    yi = max(0, min(ny - 1, int(((lat - south) / ((north - south) or 1.0)) * ny)))
    xi = max(0, min(nx - 1, int(((lon - west) / ((east - west) or 1.0)) * nx)))
    try:
        return float(arr[yi][xi])
    except Exception:
        return None


@api_bp.get("/gfs/scene")
async def api_gfs_scene():
    try:
        engine = current_app.extensions.get("gfs_engine")
        if engine is None:
            return jsonify({"ok": False, "error": "gfs_engine_unavailable"}), 503

        intent = engine.parse_intent(request.args)
        weather = await engine.weather_payload(intent)
        bbox = weather.get("bbox") or intent.bbox.as_list()
        center_lat = (bbox[1] + bbox[3]) * 0.5
        center_lon = (bbox[0] + bbox[2]) * 0.5
        fields = weather.get("fields") or {}

        temp_k = _sample_grid(fields.get("temp2m"), center_lat, center_lon, bbox)
        pressure_pa = _sample_grid(fields.get("mslp"), center_lat, center_lon, bbox)
        wind_u = _sample_grid(fields.get("wind_u"), center_lat, center_lon, bbox)
        wind_v = _sample_grid(fields.get("wind_v"), center_lat, center_lon, bbox)
        wind_speed = None
        if wind_u is not None and wind_v is not None:
            wind_speed = (wind_u * wind_u + wind_v * wind_v) ** 0.5

        return jsonify({
            "ok": True,
            "bbox": bbox,
            "valid_time": weather.get("valid_time"),
            "fields": fields,
            "hud": {
                "sample_lat": center_lat,
                "sample_lon": center_lon,
                "temperature_c": (temp_k - 273.15) if temp_k is not None else None,
                "pressure_hpa": (pressure_pa / 100.0) if pressure_pa is not None else None,
                "wind_speed_mps": wind_speed,
            },
        })
    except GfsError as exc:
        return jsonify(exc.to_json()), exc.status_code
