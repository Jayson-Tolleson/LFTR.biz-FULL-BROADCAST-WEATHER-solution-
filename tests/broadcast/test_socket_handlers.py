from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

# Minimal stubs so server modules import without heavy optional deps.
sys.modules.setdefault("quart", SimpleNamespace(Response=object))
aiortc_stub = SimpleNamespace(RTCPeerConnection=object, RTCSessionDescription=object, RTCIceCandidate=object)
sys.modules.setdefault("aiortc", aiortc_stub)
sys.modules.setdefault("aiortc.contrib", SimpleNamespace(media=SimpleNamespace(MediaRelay=object)))
sys.modules.setdefault("aiortc.contrib.media", SimpleNamespace(MediaRelay=object))
sys.modules.setdefault("aiortc.sdp", SimpleNamespace(candidate_from_sdp=lambda x: x))

from server.socket_handlers import register_socket_handlers
from server.state import AppState


class DummyRTC:
    def __init__(self):
        self.calls = []

    async def stop_broadcaster(self, room_id, sid):
        self.calls.append(("stop_broadcaster", room_id, sid))

    async def stop_viewer(self, room_id, sid):
        self.calls.append(("stop_viewer", room_id, sid))

    async def enable_stt_for_broadcaster(self, room_id, sid):
        self.calls.append(("enable_stt", room_id, sid))
        return True, "enabled"

    async def disable_stt_for_broadcaster(self, room_id, sid):
        self.calls.append(("disable_stt", room_id, sid))
        return True, "disabled"

    async def start_broadcaster_from_offer(self, room_id, sid, sdp, sdp_type):
        self.calls.append(("start_broadcaster_from_offer", room_id, sid, sdp_type))
        return {"sdp": "ans", "type": "answer"}

    async def parse_ice(self, payload):
        return payload

    def parse_ice(self, payload):
        return payload

    async def add_broadcaster_ice_candidate(self, room_id, sid, candidate):
        self.calls.append(("add_broadcaster_ice_candidate", room_id, sid, bool(candidate)))

    async def start_viewer_offer(self, room_id, sid):
        self.calls.append(("start_viewer_offer", room_id, sid))
        return {"sdp": "offer", "type": "offer"}

    async def set_viewer_answer(self, room_id, sid, sdp, sdp_type):
        self.calls.append(("set_viewer_answer", room_id, sid, sdp_type))

    async def add_viewer_ice_candidate(self, room_id, sid, candidate):
        self.calls.append(("add_viewer_ice_candidate", room_id, sid, bool(candidate)))


def test_socket_webrtc_handlers_route_signaling(fake_sio, settings_stub, monkeypatch):
    async def _run():
        state = AppState(default_room="default")
        rtc = DummyRTC()

        register_socket_handlers(fake_sio, state, settings_stub, rtc)

        # Simulate connect as broadcaster to populate sid meta and room memberships.
        await fake_sio.handlers["connect"]("sid-b", {"QUERY_STRING": "room=r1&role=broadcast"}, None)

        await fake_sio.handlers["webrtc_offer"]("sid-b", {"sdp": "abc", "type": "offer"})
        await fake_sio.handlers["webrtc_ice"]("sid-b", {"candidate": "cand"})

        assert any(c[0] == "start_broadcaster_from_offer" for c in rtc.calls)
        assert any(c[0] == "add_broadcaster_ice_candidate" for c in rtc.calls)

        # Simulate watch side signaling.
        await fake_sio.handlers["connect"]("sid-w", {"QUERY_STRING": "room=r1&role=watch"}, None)
        await fake_sio.handlers["watch_join"]("sid-w", {"room": "r1"})
        await fake_sio.handlers["watch_answer"]("sid-w", {"sdp": "xyz", "type": "answer"})
        await fake_sio.handlers["watch_ice"]("sid-w", {"candidate": "cand2"})

        assert any(c[0] == "start_viewer_offer" for c in rtc.calls)
        assert any(c[0] == "set_viewer_answer" for c in rtc.calls)
        assert any(c[0] == "add_viewer_ice_candidate" for c in rtc.calls)


    asyncio.run(_run())
def test_socket_webrtc_offer_missing_sdp_is_ignored(fake_sio, settings_stub):
    async def _run():
        state = AppState(default_room="default")
        rtc = DummyRTC()
        register_socket_handlers(fake_sio, state, settings_stub, rtc)

        await fake_sio.handlers["connect"]("sid-b", {"QUERY_STRING": "room=r1&role=broadcast"}, None)
        await fake_sio.handlers["webrtc_offer"]("sid-b", {"sdp": "", "type": "offer"})

        assert not any(c[0] == "start_broadcaster_from_offer" for c in rtc.calls)
    asyncio.run(_run())
