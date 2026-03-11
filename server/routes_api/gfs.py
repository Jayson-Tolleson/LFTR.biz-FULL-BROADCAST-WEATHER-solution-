from quart import current_app, request
from fastapi import APIRouter

router = APIRouter(prefix='/api/gfs')
compat = APIRouter(prefix='/gfs/api')


def _bbox():
    def _f(name, default):
        try:
            return float(request.args.get(name, default))
        except Exception:
            return default

    return {"west": _f("west", -180.0), "south": _f("south", -80.0), "east": _f("east", 180.0), "north": _f("north", 80.0)}


@router.get('/status')
async def gfs_status():
    gfs = current_app.state.gfs
    settings = current_app.state.settings
    payload = gfs.health()
    payload['maps3d_available'] = bool(settings.google_maps_api_key)
    return payload


@router.get('/scene')
async def gfs_scene():
    return current_app.state.gfs.cloud_tiles_payload(_bbox())


@router.get('/cloud-tiles')
async def gfs_cloud_tiles():
    return current_app.state.gfs.cloud_tiles_payload(_bbox())


@router.get('/hazards')
async def gfs_hazards():
    payload = current_app.state.gfs.cloud_tiles_payload(_bbox())
    return {
        'rain': payload.get('rain', {}),
        'hail': payload.get('hail', {}),
        'lightning': payload.get('lightning', {}),
        'canonical_shape': payload.get('canonical_shape') or payload.get('grid_shape'),
    }


@router.get('/diagnostics')
async def gfs_diagnostics():
    payload = current_app.state.gfs.cloud_tiles_payload(_bbox())
    return {
        'ok': bool(payload.get('ok', True)),
        'data_source': payload.get('data_source'),
        'payload_state': payload.get('payload_state'),
        'grid_shape': payload.get('grid_shape'),
        'canonical_shape': payload.get('canonical_shape'),
        'used_fallback': payload.get('used_fallback'),
        'fallback_reason': payload.get('fallback_reason'),
        'decode_backend': payload.get('decode_backend'),
        'data_source_mode': payload.get('data_source_mode'),
    }


@router.get('/fish')
@router.get('/points')
async def gfs_fish():
    return current_app.state.gfs.fish_payload()


@router.get('/config')
async def gfs_config():
    gfs = current_app.state.gfs
    settings = current_app.state.settings
    payload = gfs.config()
    payload['google_maps_api_key'] = settings.google_maps_api_key
    payload['mapsApiKey'] = settings.google_maps_api_key
    payload['maps3d_available'] = bool(settings.google_maps_api_key)
    return payload


@compat.get('/health')
async def gfs_health_compat():
    gfs = current_app.state.gfs
    settings = current_app.state.settings
    payload = gfs.health()
    payload['maps3d_available'] = bool(settings.google_maps_api_key)
    return payload


@compat.get('/scene')
@compat.get('/clouds')
@compat.get('/cloud_tiles')
async def gfs_scene_compat():
    return current_app.state.gfs.cloud_tiles_payload(_bbox())


@compat.get('/config')
async def gfs_config_compat():
    gfs = current_app.state.gfs
    settings = current_app.state.settings
    payload = gfs.config()
    payload['google_maps_api_key'] = settings.google_maps_api_key
    payload['mapsApiKey'] = settings.google_maps_api_key
    payload['maps3d_available'] = bool(settings.google_maps_api_key)
    return payload


@compat.get('/fish')
@compat.get('/points')
async def gfs_fish_compat():
    return current_app.state.gfs.fish_payload()
