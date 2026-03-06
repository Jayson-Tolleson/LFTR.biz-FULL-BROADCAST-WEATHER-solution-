from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.contrib.media import MediaRelay
from aiortc.sdp import candidate_from_sdp

from server.state import AppState
from server.utils import now_ms


log = logging.getLogger("server.rtc")


@dataclass
class BroadcasterSession:
    sid: str
    pc: RTCPeerConnection
    tracks: Dict[str, Any]


class RTCManager:
    def __init__(self, state: AppState) -> None:
        self.state = state
        self.relay = MediaRelay()
        self.broadcasters: Dict[str, BroadcasterSession] = {}
        self.viewers: Dict[str, Dict[str, RTCPeerConnection]] = {}
        self._pending_cleanup: Dict[str, asyncio.Task] = {}
        self.disconnect_grace_seconds = 60

    async def _emit_room(self, room_id: str, event: str, payload: Dict[str, Any]) -> None:
        if not self.state.sio:
            return
        await self.state.sio.emit(event, payload, to=f"room:{room_id}:all")

    async def _emit_status(self, room_id: str) -> None:
        if not self.state.sio:
            return
        room = self.state.ensure_room(room_id)
        await self._emit_room(
            room_id,
            "room_status",
            {
                "room": room_id,
                "viewer_count": len(room.viewers),
                "broadcaster_present": room.broadcaster_sid is not None,
                "ai": {"enabled": room.settings.ai_enabled, "power": True, "mode": "active", "fallback": True},
                "stt_enabled": room.settings.stt_enabled,
                "tts_enabled": room.settings.tts_enabled,
                "ts": now_ms(),
            },
        )

    async def _wait_ice_complete(self, pc: RTCPeerConnection, timeout_s: float = 5.0) -> None:
        if pc.iceGatheringState == "complete":
            return
        done = asyncio.Event()

        @pc.on("icegatheringstatechange")
        def _on_state() -> None:
            if pc.iceGatheringState == "complete":
                done.set()

        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            pass

    async def _schedule_cleanup(self, room_id: str, sid: str, role: str) -> None:
        key = f"{room_id}:{sid}:{role}"
        prev = self._pending_cleanup.get(key)
        if prev:
            prev.cancel()

        async def _runner() -> None:
            await asyncio.sleep(self.disconnect_grace_seconds)
            if role == "broadcaster":
                b = self.broadcasters.get(room_id)
                if b and b.sid == sid and b.pc.connectionState == "disconnected":
                    await self.stop_broadcaster(room_id, sid)
            else:
                vpc = self.viewers.get(room_id, {}).get(sid)
                if vpc and vpc.connectionState == "disconnected":
                    await self.stop_viewer(room_id, sid)

        self._pending_cleanup[key] = asyncio.create_task(_runner())

    async def start_broadcaster_from_offer(self, room_id: str, sid: str, sdp: str, sdp_type: str) -> Dict[str, str]:
        existing = self.broadcasters.get(room_id)
        if existing:
            await self.stop_broadcaster(room_id, existing.sid)

        pc = RTCPeerConnection()
        session = BroadcasterSession(sid=sid, pc=pc, tracks={})
        self.broadcasters[room_id] = session
        room = self.state.ensure_room(room_id)
        room.broadcaster_sid = sid

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange() -> None:
            st = pc.connectionState
            log.info("broadcaster state room=%s sid=%s state=%s", room_id, sid, st)
            await self._emit_room(room_id, "webrtc_state", {"room": room_id, "role": "broadcaster", "state": st, "ts": now_ms()})
            if st == "disconnected":
                await self._schedule_cleanup(room_id, sid, "broadcaster")
            if st in {"failed", "closed"}:
                await self.stop_broadcaster(room_id, sid)

        @pc.on("track")
        async def _on_track(track: Any) -> None:
            session.tracks[track.kind] = track
            await self._emit_room(room_id, "stream_started", {"room": room_id, "kind": track.kind, "ts": now_ms()})

        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_ice_complete(pc)
        await self._emit_status(room_id)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def start_viewer_offer(self, room_id: str, sid: str) -> Dict[str, str]:
        pc = RTCPeerConnection()
        self.viewers.setdefault(room_id, {})[sid] = pc
        room = self.state.ensure_room(room_id)
        room.viewers[sid] = pc

        @pc.on("connectionstatechange")
        async def _on_connectionstatechange() -> None:
            st = pc.connectionState
            log.info("viewer state room=%s sid=%s state=%s", room_id, sid, st)
            await self._emit_room(room_id, "webrtc_state", {"room": room_id, "role": "watch", "state": st, "ts": now_ms()})
            if st == "disconnected":
                await self._schedule_cleanup(room_id, sid, "viewer")
            if st in {"failed", "closed"}:
                await self.stop_viewer(room_id, sid)

        b = self.broadcasters.get(room_id)
        if b:
            for _kind, tr in b.tracks.items():
                try:
                    pc.addTrack(self.relay.subscribe(tr))
                except Exception:
                    log.exception("failed to add relayed track")

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        await self._wait_ice_complete(pc)
        await self._emit_status(room_id)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def set_viewer_answer(self, room_id: str, sid: str, sdp: str, sdp_type: str) -> None:
        pc = self.viewers.get(room_id, {}).get(sid)
        if not pc:
            raise RuntimeError("Viewer peer not found")
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))

    def parse_ice(self, payload: Dict[str, Any]) -> Optional[RTCIceCandidate]:
        payload = payload or {}
        cand = payload.get("candidate")
        if isinstance(cand, dict):
            nested = cand
            cand = nested.get("candidate") or ""
            payload = {**payload, **nested}
        if not cand:
            return None
        if isinstance(cand, str) and cand.startswith("candidate:"):
            cand = cand[len("candidate:"):]
        c = candidate_from_sdp(str(cand))
        c.sdpMid = payload.get("sdpMid")
        c.sdpMLineIndex = payload.get("sdpMLineIndex")
        return c

    async def add_broadcaster_ice_candidate(self, room_id: str, sid: str, candidate: Optional[RTCIceCandidate]) -> None:
        b = self.broadcasters.get(room_id)
        if not b or b.sid != sid:
            return
        await b.pc.addIceCandidate(candidate)

    async def add_viewer_ice_candidate(self, room_id: str, sid: str, candidate: Optional[RTCIceCandidate]) -> None:
        pc = self.viewers.get(room_id, {}).get(sid)
        if not pc:
            return
        await pc.addIceCandidate(candidate)

    async def stop_viewer(self, room_id: str, sid: str) -> None:
        pc = self.viewers.get(room_id, {}).pop(sid, None)
        room = self.state.ensure_room(room_id)
        room.viewers.pop(sid, None)
        if pc:
            try:
                await pc.close()
            except Exception:
                log.exception("error closing viewer pc")
        self.state.remove_room_if_empty(room_id)
        await self._emit_status(room_id)

    async def stop_broadcaster(self, room_id: str, sid: str) -> None:
        b = self.broadcasters.get(room_id)
        if not b or b.sid != sid:
            return
        try:
            await b.pc.close()
        except Exception:
            log.exception("error closing broadcaster pc")
        self.broadcasters.pop(room_id, None)

        room = self.state.ensure_room(room_id)
        room.broadcaster_sid = None

        viewers = list(self.viewers.get(room_id, {}).keys())
        for vsid in viewers:
            await self.stop_viewer(room_id, vsid)

        await self._emit_room(room_id, "stream_stopped", {"room": room_id, "ts": now_ms()})
        await self._emit_status(room_id)
