from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from quart import jsonify, request, send_from_directory

from . import config
from .broadcast.video_archive import VideoArchive
from .broadcast.webrtc_ingest import WebRTCIngest
from .marine.prediction.bait_predictor import BaitPredictor
from .marine.prediction.prediction_routes import register_prediction_routes
from .weather.gfs_service import GFSService


def compact_tiles(items):
    return [
        {
            'lat': i['lat'],
            'lng': i['lng'],
            'b': i['base_altitude_m'],
            't': i['top_altitude_m'],
            'c': i['coverage'],
            'd': i['density'],
            'p': i['precip_rate'],
            's': i['storm_energy'],
            'u': i['wind_u'],
            'v': i['wind_v'],
            'i': i['importance'],
        }
        for i in items
    ]


def register_routes(app):
    gfs = GFSService()
    archive = VideoArchive(config.VIDEO_ROOT)
    ingest = WebRTCIngest(archive)
    fish_csv = Path(__file__).resolve().parent.parent / 'data' / 'fishloclist.csv'
    predictor = BaitPredictor(config.MODEL_DIR / 'bait_predictor.json', fish_csv, config.REPORT_EVENTS_FILE)

    @app.get('/')
    async def index():
        return await send_from_directory(app.static_folder, 'indexgfs.html')

    @app.get('/indexgfs')
    async def indexgfs():
        return await send_from_directory(app.static_folder, 'indexgfs.html')

    @app.get('/broadcast')
    async def broadcast_page():
        return await send_from_directory(app.static_folder, 'broadcast.html')

    @app.get('/gfs/api/config')
    async def api_config():
        return jsonify({'googleMapsApiKey': config.GOOGLE_MAPS_API_KEY, 'maxCloudTiles': config.MAX_CLOUD_TILES})

    @app.get('/gfs/api/cloud_tiles')
    async def cloud_tiles():
        limit = max(1, min(int(request.args.get('limit', 200)), config.MAX_CLOUD_TILES))
        compact = request.args.get('compact', '1') == '1'
        payload = gfs.cloud_tiles()
        items = payload['items'][:limit]
        if compact:
            items = compact_tiles(items)
        return jsonify({'items': items, 'generated_at': payload['generated_at'], 'compact': compact})


    @app.get('/api/gfs')
    async def api_gfs():
        payload = gfs.cloud_tiles()
        return jsonify({'items': payload['items'][:200], 'generated_at': payload['generated_at']})

    @app.get('/gfs/api/jetstream')
    async def jetstream():
        return jsonify(gfs.jetstream())

    @app.get('/gfs/api/wind')
    async def wind():
        return jsonify(gfs.currents())

    @app.get('/marine/api/swell')
    async def swell():
        return jsonify(gfs.swell())

    @app.get('/marine/api/bait')
    async def bait():
        return jsonify(gfs.bait())

    @app.post('/broadcast/api/start')
    async def broadcast_start():
        body = await request.get_json(force=True)
        stream_id = body.get('stream_id') or datetime.now(timezone.utc).strftime('stream-%H%M%S')
        lat = float(body.get('lat', 0))
        lng = float(body.get('lng', 0))
        stream = ingest.start_stream(stream_id, lat, lng)

        config.REPORT_EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with config.REPORT_EVENTS_FILE.open('a', encoding='utf-8') as f:
            f.write(json.dumps({'lat': lat, 'lng': lng, 'timestamp': datetime.now(timezone.utc).isoformat(), 'bait_presence': 1}) + '\n')

        return jsonify({'status': 'active', 'stream_id': stream.stream_id, 'path': str(stream.output_path)})

    @app.post('/broadcast/api/stop')
    async def broadcast_stop():
        body = await request.get_json(force=True)
        stream_id = body.get('stream_id')
        stream = ingest.stop_stream(stream_id)
        return jsonify({'status': 'stopped' if stream else 'not_found', 'stream_id': stream_id})

    @app.get('/broadcast/api/status')
    async def broadcast_status():
        return jsonify({'active_streams': [{'stream_id': s.stream_id, 'lat': s.lat, 'lng': s.lng} for s in ingest.active.values()]})

    @app.get('/broadcast/api/archive')
    async def broadcast_archive():
        lat = float(request.args.get('lat', 0))
        lng = float(request.args.get('lng', 0))
        return jsonify({'items': archive.list_clips(lat, lng)})

    register_prediction_routes(app, predictor, gfs.forecast_cells)
