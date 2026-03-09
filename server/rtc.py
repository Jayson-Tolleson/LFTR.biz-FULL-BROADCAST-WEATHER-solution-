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
        self._pending_broadcaster_ice: Dict[tuple[str, str], list[RTCIceCandidate]] = {}
        self._pending_viewer_ice: Dict[tuple[str, str], list[RTCIceCandidate]] = {}

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
                "ai": {"enabled": room.settings.ai_enabled, "power": True, "mode": room.settings.ai_mode, "fallback": True},
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



    def _broadcaster_ice_key(self, room_id: str, sid: str) -> tuple[str, str]:
        return room_id, sid

    def _viewer_ice_key(self, room_id: str, sid: str) -> tuple[str, str]:
        return room_id, sid

    async def _flush_broadcaster_ice(self, room_id: str, sid: str, pc: RTCPeerConnection) -> None:
        key = self._broadcaster_ice_key(room_id, sid)
        queued = self._pending_broadcaster_ice.pop(key, [])
        if queued:
            log.info("flush broadcaster ICE room=%s sid=%s count=%s", room_id, sid, len(queued))
        for cand in queued:
            try:
                await pc.addIceCandidate(cand)
            except Exception:
                log.exception("failed queued broadcaster ICE apply")

    async def _flush_viewer_ice(self, room_id: str, sid: str, pc: RTCPeerConnection) -> None:
        key = self._viewer_ice_key(room_id, sid)
        queued = self._pending_viewer_ice.pop(key, [])
        if queued:
            log.info("flush viewer ICE room=%s sid=%s count=%s", room_id, sid, len(queued))
        for cand in queued:
            try:
                await pc.addIceCandidate(cand)
            except Exception:
                log.exception("failed queued viewer ICE apply")

    async def enable_stt_for_broadcaster(self, room_id: str, sid: str) -> tuple[bool, str]:
        room = self.state.ensure_room(room_id)
        if room.broadcaster_sid != sid:
            return False, "not_broadcaster"
        room.settings.stt_enabled = True
        await self._emit_room(room_id, "stt_status", {"enabled": True, "reason": "enabled", "ts": now_ms()})
        await self._emit_status(room_id)
        return True, "enabled"

    async def disable_stt_for_broadcaster(self, room_id: str, sid: str) -> tuple[bool, str]:
        room = self.state.ensure_room(room_id)
        if room.broadcaster_sid != sid:
            return False, "not_broadcaster"
        room.settings.stt_enabled = False
        await self._emit_room(room_id, "stt_status", {"enabled": False, "reason": "disabled", "ts": now_ms()})
        await self._emit_status(room_id)
        return True, "disabled"

    async def start_broadcaster_from_offer(self, room_id: str, sid: str, sdp: str, sdp_type: str) -> Dict[str, str]:
        existing = self.broadcasters.get(room_id)
        if existing and existing.sid != sid:
            await self.stop_broadcaster(room_id, existing.sid)
            existing = None
        stale = [k for k in self._pending_broadcaster_ice.keys() if k[0] == room_id and k[1] != sid]
        for k in stale:
            self._pending_broadcaster_ice.pop(k, None)

        if existing:
            pc = existing.pc
            session = existing
        else:
            pc = RTCPeerConnection()
            session = BroadcasterSession(sid=sid, pc=pc, tracks={})
            self.broadcasters[room_id] = session

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

        room = self.state.ensure_room(room_id)
        room.broadcaster_sid = sid

        if pc.signalingState != "stable":
            log.info("broadcaster offer while signaling=%s; resetting peer for room=%s", pc.signalingState, room_id)
            await self.stop_broadcaster(room_id, sid)
            return await self.start_broadcaster_from_offer(room_id, sid, sdp, sdp_type)

        log.info("apply viewer remote answer room=%s sid=%s state=%s", room_id, sid, pc.signalingState)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        await self._flush_broadcaster_ice(room_id, sid, pc)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await self._wait_ice_complete(pc)
        await self._emit_status(room_id)
        return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

    async def start_viewer_offer(self, room_id: str, sid: str) -> Dict[str, str]:
        prev = self.viewers.get(room_id, {}).get(sid)
        if prev:
            try:
                await prev.close()
            except Exception:
                log.exception("error closing previous viewer pc")
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
        log.info("apply viewer remote answer room=%s sid=%s state=%s", room_id, sid, pc.signalingState)
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type=sdp_type))
        await self._flush_viewer_ice(room_id, sid, pc)

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
        key = self._broadcaster_ice_key(room_id, sid)
        if not b or b.sid != sid:
            if candidate is not None:
                self._pending_broadcaster_ice.setdefault(key, []).append(candidate)
                log.info("queue broadcaster ICE (session pending) room=%s sid=%s count=%s", room_id, sid, len(self._pending_broadcaster_ice.get(key, [])))
            return
        if candidate is None:
            await b.pc.addIceCandidate(None)
            return
        if b.pc.remoteDescription is None:
            self._pending_broadcaster_ice.setdefault(key, []).append(candidate)
            log.info("queue broadcaster ICE (remoteDescription pending) room=%s sid=%s count=%s", room_id, sid, len(self._pending_broadcaster_ice.get(key, [])))
            return
        await b.pc.addIceCandidate(candidate)

    async def add_viewer_ice_candidate(self, room_id: str, sid: str, candidate: Optional[RTCIceCandidate]) -> None:
        pc = self.viewers.get(room_id, {}).get(sid)
        key = self._viewer_ice_key(room_id, sid)
        if not pc:
            if candidate is not None:
                self._pending_viewer_ice.setdefault(key, []).append(candidate)
                log.info("queue viewer ICE (session pending) room=%s sid=%s count=%s", room_id, sid, len(self._pending_viewer_ice.get(key, [])))
            return
        if candidate is None:
            await pc.addIceCandidate(None)
            return
        if pc.remoteDescription is None:
            self._pending_viewer_ice.setdefault(key, []).append(candidate)
            log.info("queue viewer ICE (remoteDescription pending) room=%s sid=%s count=%s", room_id, sid, len(self._pending_viewer_ice.get(key, [])))
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
        self._pending_viewer_ice.pop(self._viewer_ice_key(room_id, sid), None)
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
        self._pending_broadcaster_ice.pop(self._broadcaster_ice_key(room_id, sid), None)

        room = self.state.ensure_room(room_id)
        room.broadcaster_sid = None

        viewers = list(self.viewers.get(room_id, {}).keys())
        for vsid in viewers:
            await self.stop_viewer(room_id, vsid)

        await self._emit_room(room_id, "stream_stopped", {"room": room_id, "ts": now_ms()})
        await self._emit_status(room_id)
