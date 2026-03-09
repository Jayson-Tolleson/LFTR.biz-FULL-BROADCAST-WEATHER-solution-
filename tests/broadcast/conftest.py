from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from types import SimpleNamespace

# Lightweight import stubs for optional runtime deps in test env.
aiortc_stub = SimpleNamespace(RTCPeerConnection=object, RTCSessionDescription=object, RTCIceCandidate=object)
sys.modules.setdefault("aiortc", aiortc_stub)
sys.modules.setdefault("aiortc.contrib", SimpleNamespace(media=SimpleNamespace(MediaRelay=object)))
sys.modules.setdefault("aiortc.contrib.media", SimpleNamespace(MediaRelay=object))
sys.modules.setdefault("aiortc.sdp", SimpleNamespace(candidate_from_sdp=lambda x: x))

from types import SimpleNamespace

import pytest


@dataclass
class DummyDesc:
    sdp: str
    type: str


class FakePC:
    def __init__(self):
        self.signalingState = "stable"
        self.iceGatheringState = "complete"
        self.connectionState = "new"
        self.iceConnectionState = "new"
        self.remoteDescription = None
        self.localDescription = None
        self.handlers = {}
        self.added_candidates = []
        self.tracks = []
        self.offer_count = 0
        self.answer_count = 0
        self.closed = False

    def on(self, event_name: str):
        def _decorator(fn):
            self.handlers[event_name] = fn
            return fn

        return _decorator

    async def setRemoteDescription(self, desc):
        self.remoteDescription = desc
        if getattr(desc, "type", "") == "offer":
            self.signalingState = "have-remote-offer"
        elif getattr(desc, "type", "") == "answer":
            self.signalingState = "stable"

    async def setLocalDescription(self, desc):
        self.localDescription = desc
        if getattr(desc, "type", "") == "offer":
            self.signalingState = "have-local-offer"
        elif getattr(desc, "type", "") == "answer":
            self.signalingState = "stable"

    async def createOffer(self):
        self.offer_count += 1
        return DummyDesc(sdp=f"offer-{self.offer_count}", type="offer")

    async def createAnswer(self):
        self.answer_count += 1
        return DummyDesc(sdp=f"answer-{self.answer_count}", type="answer")

    async def addIceCandidate(self, candidate):
        self.added_candidates.append(candidate)

    def addTrack(self, track):
        self.tracks.append(track)

    async def close(self):
        self.closed = True
        self.connectionState = "closed"


class FakeRelay:
    def subscribe(self, track):
        return track


@pytest.fixture
def rtc_patched(monkeypatch):
    import server.rtc as rtc_module

    monkeypatch.setattr(rtc_module, "RTCPeerConnection", FakePC)
    monkeypatch.setattr(rtc_module, "RTCSessionDescription", DummyDesc)
    monkeypatch.setattr(rtc_module, "MediaRelay", FakeRelay)
    return rtc_module


class FakeSIO:
    def __init__(self):
        self.handlers = {}
        self.emitted = []
        self.rooms = []

    def event(self, fn):
        self.handlers[fn.__name__] = fn
        return fn

    def on(self, name):
        def _decorator(fn):
            self.handlers[name] = fn
            return fn

        return _decorator

    async def emit(self, event, payload, to=None):
        self.emitted.append((event, payload, to))

    async def enter_room(self, sid, room):
        self.rooms.append((sid, room))


@pytest.fixture
def fake_sio():
    return FakeSIO()


@pytest.fixture
def settings_stub():
    from server.config import Settings

    return Settings(
        debug=True,
        socket_path="/socket.io",
        static_dir="static",
        default_room="default",
        max_room_len=48,
        max_upload_bytes=8 * 1024 * 1024,
        ai_enabled=True,
        ai_fallback_text="fallback",
        turn_url="",
        turn_urls="",
        turns_url="",
        turn_username="",
        turn_password="",
        domain="",
        public_ip="",
        google_maps_api_key="",
    )
