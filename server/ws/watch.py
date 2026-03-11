from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from quart import websocket

from server.state import AppState
from server.utils import now_ms

log = logging.getLogger("server.ws.watch")


class WSRegistry:
    def __init__(self) -> None:
        self.rooms: dict[str, dict[str, dict[Any, str]]] = defaultdict(lambda: {"chat": {}, "broadcast": {}, "watch": {}})

    def register(self, room: str, kind: str, ws: Any, client_id: str) -> None:
        self.rooms[room][kind][ws] = client_id

    def unregister(self, room: str, kind: str, ws: Any) -> str | None:
        bucket = self.rooms.get(room, {}).get(kind, {})
        cid = bucket.pop(ws, None)
        if room in self.rooms and not any(self.rooms[room][k] for k in ("chat", "broadcast", "watch")):
            self.rooms.pop(room, None)
        return cid

    def viewer_count(self, room: str) -> int:
        return len(self.rooms.get(room, {}).get("watch", {}))

    async def broadcast_room(self, room: str, message: dict[str, Any], kinds=("chat", "broadcast", "watch")):
        dead: list[tuple[str, Any]] = []
        room_obj = self.rooms.get(room, {})
        for kind in kinds:
            for ws in list(room_obj.get(kind, {}).keys()):
                try:
                    await ws.send_json(message)
                except Exception:
                    dead.append((kind, ws))
        for kind, ws in dead:
            self.unregister(room, kind, ws)


registry = WSRegistry()

async def _recv_json() -> dict[str, Any]:
    raw = await websocket.receive()
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode('utf-8', errors='ignore')
    if isinstance(raw, str):
        try:
            import json

            return json.loads(raw or '{}')
        except Exception:
            return {}
    return {}



def _normalize_room_client(payload: dict[str, Any], default_room: str, role: str) -> tuple[str, str]:
    room = str(payload.get("room") or payload.get("payload", {}).get("room") or default_room or "default")
    client_id = str(payload.get("clientId") or payload.get("payload", {}).get("clientId") or f"{role}:{id(payload)}")
    return room, client_id


async def _broadcast_presence(state: AppState, room_id: str) -> None:
    room = state.ensure_room(room_id)
    room.runtime.viewer_count = registry.viewer_count(room_id)
    room.runtime.broadcaster_present = room.broadcaster_sid is not None
    room.runtime.broadcast_connected = room.broadcaster_sid is not None
    room.runtime.chat_connected = bool(registry.rooms.get(room_id, {}).get("chat"))
    room.runtime.watch_connected = room.runtime.viewer_count > 0
    await registry.broadcast_room(room_id, {"type": "presence", "room": room_id, "broadcaster_present": room.runtime.broadcaster_present, "viewer_count": room.runtime.viewer_count, "ts": now_ms()})
    snapshot = state.room_state_payload(room_id)
    await registry.broadcast_room(room_id, {"type": "state_sync", "room": room_id, "state": snapshot, "ts": now_ms()})


def init_ws_routes(app, state: AppState, rtc) -> None:
    @app.websocket('/ws/watch')
    async def ws_watch():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"watch:{id(ws)}"
        joined = False
        try:
            while True:
                payload = await _recv_json()
                kind = payload.get("type", "join")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                if kind in {"join", "watch_join"}:
                    room_id, client_id = _normalize_room_client(data, state.default_room, "viewer")
                    registry.register(room_id, "watch", ws, client_id)
                    joined = True
                    state.ensure_room(room_id).viewers[client_id] = True
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await _broadcast_presence(state, room_id)
                    room = state.ensure_room(room_id)
                    if room.broadcaster_sid is None:
                        await ws.send_json({"type": "waiting", "room": room_id, "message": "no_broadcaster", "ts": now_ms()})
                    else:
                        offer = await rtc.start_viewer_offer(room_id, client_id) if rtc else None
                        if offer:
                            await ws.send_json({"type": "watch_offer", "room": room_id, "payload": offer, "ts": now_ms()})
                    continue
                if kind == "leave":
                    break
                if kind == "ping":
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == "request_stream":
                    room = state.ensure_room(room_id)
                    if room.broadcaster_sid is None:
                        await ws.send_json({"type": "waiting", "room": room_id, "message": "no_broadcaster", "ts": now_ms()})
                    elif rtc:
                        offer = await rtc.start_viewer_offer(room_id, client_id)
                        await ws.send_json({"type": "watch_offer", "room": room_id, "payload": offer, "ts": now_ms()})
                elif kind in {"watch_answer", "webrtc_answer"} and rtc:
                    sdp = data.get("sdp")
                    sdp_type = data.get("type") or "answer"
                    if sdp:
                        await rtc.set_viewer_answer(room_id, client_id, sdp, sdp_type)
                elif kind in {"webrtc_ice", "watch_ice"} and rtc:
                    cand = rtc.parse_ice(data or {})
                    await rtc.add_viewer_ice_candidate(room_id, client_id, cand)
        finally:
            if joined:
                registry.unregister(room_id, "watch", ws)
                state.ensure_room(room_id).viewers.pop(client_id, None)
                if rtc:
                    try:
                        await rtc.stop_viewer(room_id, client_id)
                    except Exception:
                        log.exception("watch cleanup failed")
                await _broadcast_presence(state, room_id)

    @app.websocket('/ws/broadcast')
    async def ws_broadcast():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"broadcast:{id(ws)}"
        joined = False
        try:
            while True:
                payload = await _recv_json()
                kind = payload.get("type", "ping")
                data = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
                if kind == 'join':
                    room_id, client_id = _normalize_room_client(data, state.default_room, 'broadcaster')
                    registry.register(room_id, 'broadcast', ws, client_id)
                    joined = True
                    room = state.ensure_room(room_id)
                    room.broadcaster_sid = client_id
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await _broadcast_presence(state, room_id)
                    continue
                if kind == 'ping':
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == 'webrtc_offer' and rtc:
                    sdp = data.get('sdp')
                    sdp_type = data.get('type') or 'offer'
                    if sdp:
                        answer = await rtc.start_broadcaster_from_offer(room_id, client_id, sdp, sdp_type)
                        await ws.send_json({"type": "webrtc_answer", "room": room_id, "clientId": client_id, "sdp": answer.get('sdp'), "answerType": answer.get('type', 'answer'), "ts": now_ms()})
                elif kind in {'webrtc_ice', 'watch_ice'} and rtc:
                    cand = rtc.parse_ice(data or {})
                    await rtc.add_broadcaster_ice_candidate(room_id, client_id, cand)
        finally:
            if joined:
                registry.unregister(room_id, 'broadcast', ws)
                room = state.ensure_room(room_id)
                if room.broadcaster_sid == client_id:
                    room.broadcaster_sid = None
                if rtc:
                    await rtc.stop_broadcaster(room_id, client_id)
                await _broadcast_presence(state, room_id)

    @app.websocket('/ws/chat')
    async def ws_chat():
        ws = websocket._get_current_object()
        room_id = state.default_room
        client_id = f"chat:{id(ws)}"
        joined = False
        try:
            while True:
                payload = await _recv_json()
                kind = payload.get('type', 'ping')
                data = payload.get('payload') if isinstance(payload.get('payload'), dict) else payload
                if kind == 'join':
                    room_id, client_id = _normalize_room_client(data, state.default_room, 'participant')
                    registry.register(room_id, 'chat', ws, client_id)
                    joined = True
                    await ws.send_json({"type": "state_sync", "room": room_id, "state": state.room_state_payload(room_id), "ts": now_ms()})
                    await _broadcast_presence(state, room_id)
                    continue
                if kind == 'ping':
                    await ws.send_json({"type": "pong", "room": room_id, "ts": now_ms()})
                elif kind == 'chat':
                    text = str(data.get('text') or '').strip()
                    if text:
                        await registry.broadcast_room(room_id, {"type": "chat", "room": room_id, "user": "participant", "clientId": client_id, "text": text, "ts": now_ms()})
        finally:
            if joined:
                registry.unregister(room_id, 'chat', ws)
                await _broadcast_presence(state, room_id)
