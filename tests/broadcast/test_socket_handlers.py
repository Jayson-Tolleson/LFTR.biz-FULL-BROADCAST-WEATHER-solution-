from __future__ import annotations

from pathlib import Path

from server.state import get_default_room_state


def test_app_factory_is_raw_quart_only_source_contract():
    src = Path('server/app_factory.py').read_text(encoding='utf-8')
    assert 'import socketio' not in src
    assert 'ASGIApp(' not in src
    assert 'register_socket_handlers' not in src
    assert 'def create_app()' in src
    assert 'return create_quart_app()' in src


def test_broadcast_routes_drive_rtc_manager_contract():
    src = Path('server/broadcast/routes.py').read_text(encoding='utf-8')
    assert "@app.websocket('/ws/broadcast')" in src
    assert "@app.websocket('/ws/watch')" in src
    assert "@app.websocket('/ws/chat')" in src
    assert 'start_broadcaster_from_offer' in src
    assert 'add_broadcaster_ice_candidate' in src
    assert 'start_viewer_offer' in src
    assert 'set_viewer_answer' in src
    assert 'add_viewer_ice_candidate' in src


def test_default_room_state_preserves_rich_controls_on_by_default():
    room = get_default_room_state()
    assert room.settings.ai_enabled is True
    assert room.settings.ai_status == 'idle'
    assert room.settings.stt_enabled is True
    assert room.settings.hear_ai_voice is True
    assert room.settings.mic_enabled is True
    assert room.settings.camera_enabled is True
    assert room.settings.noise_cancel_enabled is True


def test_broadcast_page_has_single_stt_pill():
    html = Path('static/broadcast.html').read_text(encoding='utf-8')
    assert html.count('id="sttBtn"') == 1
    assert 'id="sstBtn"' not in html


def test_chat_supports_web_search_and_attachment_message_types():
    src = Path('server/broadcast/routes.py').read_text(encoding='utf-8')
    assert 'web_search' in src
    assert 'web_search_result' in src
    assert 'attachment_uploaded' in src
    assert 'attachment' in src


def test_watch_request_stream_and_audio_chunk_contracts_present():
    src = Path('server/broadcast/routes.py').read_text(encoding='utf-8')
    assert 'request_stream' in src
    assert 'audio_chunk' in src
    assert '"source": "stt"' in src or "'source': 'stt'" in src


def test_request_stream_only_in_watch_socket_flow():
    src = Path('server/broadcast/routes.py').read_text(encoding='utf-8')
    b_start = src.index("@app.websocket('/ws/broadcast')")
    w_start = src.index("@app.websocket('/ws/watch')")
    broadcast_block = src[b_start:w_start]
    watch_block = src[w_start:]
    assert 'request_stream' not in broadcast_block
    assert 'request_stream' in watch_block


def test_watch_socket_stays_open_without_broadcaster():
    quart = __import__('pytest').importorskip('quart')
    import asyncio
    from server.app_factory import create_quart_app

    async def _run():
        app = create_quart_app()
        client = app.test_client()
        async with client.websocket('/ws/watch') as ws:
            await ws.send_json({'type': 'join', 'room': 'default', 'clientId': 'viewer-test', 'role': 'viewer'})
            saw_waiting = False
            for _ in range(6):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2)
                if msg.get('type') == 'error' and msg.get('message') == 'no_broadcaster':
                    saw_waiting = True
                    break
                if msg.get('type') == 'presence' and msg.get('broadcaster_present') is False:
                    saw_waiting = True
                    break
            assert saw_waiting
            await ws.send_json({'type': 'ping', 'room': 'default', 'clientId': 'viewer-test'})
            got_pong = False
            for _ in range(8):
                msg = await asyncio.wait_for(ws.receive_json(), timeout=2)
                if msg.get('type') == 'pong':
                    got_pong = True
                    break
            assert got_pong

    asyncio.run(_run())
