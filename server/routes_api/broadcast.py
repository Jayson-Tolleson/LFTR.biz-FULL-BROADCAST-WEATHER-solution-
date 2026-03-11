from quart import current_app
from fastapi import APIRouter

router = APIRouter()


def _normalize_ice_url(raw: str, default_scheme: str = 'turn') -> str:
    val = (raw or '').strip()
    if not val:
        return ''
    if val.startswith(('stun:', 'turn:', 'turns:')):
        return val
    host = val
    port = '3478'
    if ':' in val and '?' not in val:
        maybe_host, maybe_port = val.rsplit(':', 1)
        if maybe_port.isdigit():
            host, port = maybe_host, maybe_port
    if default_scheme == 'turns':
        if port == '3478':
            port = '5349'
        return f'turns:{host}:{port}'
    return f'turn:{host}:{port}?transport=udp'


def build_ice_servers(settings):
    servers = [{"urls": ["stun:stun.l.google.com:19302"]}]
    raw_urls = []
    if settings.turn_url:
        raw_urls.append(_normalize_ice_url(settings.turn_url, 'turn'))
    if settings.turns_url:
        raw_urls.append(_normalize_ice_url(settings.turns_url, 'turns'))
    for raw in settings.turn_urls.split(','):
        raw = raw.strip()
        if raw:
            raw_urls.append(_normalize_ice_url(raw, 'turn'))
    host = settings.domain or settings.public_ip
    if host:
        raw_urls.extend([f"turn:{host}:3478?transport=udp", f"turn:{host}:3478?transport=tcp", f"turns:{host}:5349"])
    turn_urls = []
    seen = set()
    for url in raw_urls:
        u = (url or '').strip()
        if not u or u in seen:
            continue
        seen.add(u)
        turn_urls.append(u)
    if turn_urls and settings.turn_username and settings.turn_password:
        servers.append({"urls": turn_urls, "username": settings.turn_username, "credential": settings.turn_password})
    return servers


@router.get('/webrtc/ice-config')
async def webrtc_ice_config():
    settings = current_app.state.settings
    return {'iceServers': build_ice_servers(settings)}


@router.get('/api/rooms/<room_id>/state')
async def room_state(room_id: str):
    return current_app.state.app_state.room_state_payload(room_id)
