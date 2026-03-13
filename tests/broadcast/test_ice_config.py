from server.config import Settings
from server.routes_core import build_ice_servers


def _settings(**kwargs):
    base = dict(
        debug=False,
        socket_path="/socket.io",
        static_dir="static",
        default_room="default",
        max_room_len=48,
        max_upload_bytes=8 * 1024 * 1024,
        ai_enabled=True,
        ai_fallback_text="AI unavailable",
        turn_url="",
        turn_urls="",
        turns_url="",
        turn_username="",
        turn_password="",
        domain="",
        public_ip="",
        google_maps_api_key="",
    )
    base.update(kwargs)
    return Settings(**base)


def test_ice_servers_stun_only_without_turn_credentials():
    settings = _settings(turn_url="turn:turn.example.com:3478?transport=udp", domain="example.com")
    servers = build_ice_servers(settings)
    assert len(servers) == 1
    assert servers[0]["urls"] == ["stun:stun.l.google.com:19302"]


def test_ice_servers_include_turn_with_credentials_and_dedupe_urls():
    settings = _settings(
        turn_url="turn:turn.example.com:3478?transport=udp",
        turn_urls="turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=udp",
        turn_username="u",
        turn_password="p",
        domain="example.com",
    )
    servers = build_ice_servers(settings)
    assert len(servers) == 2
    turn = servers[1]
    assert turn["username"] == "u"
    assert turn["credential"] == "p"
    assert len(turn["urls"]) == len(set(turn["urls"]))
