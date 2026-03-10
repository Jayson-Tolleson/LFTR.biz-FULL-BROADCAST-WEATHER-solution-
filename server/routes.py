import asyncio
import json

from quart import Blueprint, jsonify, websocket

from server import ai
from server.gfs_service import fallback_allowed, generate_live_payload
from server.rtc import state

bp = Blueprint("routes", __name__)


@bp.get("/health")
async def health():
    return jsonify({"ok": True})


@bp.get("/ai/status")
async def ai_status():
    return jsonify({"gemini": ai.gemini_readiness(), "speech": ai.speech_readiness()})


@bp.get("/gfs/payload")
async def gfs_payload():
    cloud = [[0]*1440 for _ in range(721)]
    precip = [[0]*1440 for _ in range(721)]
    return jsonify(generate_live_payload(cloud, precip, allow_synthetic_fallback=fallback_allowed()))


@bp.websocket("/watch")
async def watch_ws():
    backoff_ms = 750
    try:
        async with state.lock:
            state.viewers.add(id(websocket))
        while True:
            if state.broadcaster is None:
                await websocket.send(json.dumps({"type": "waiting_for_broadcaster", "retry_ms": backoff_ms}))
                await asyncio.sleep(backoff_ms / 1000)
                backoff_ms = min(5000, int(backoff_ms * 1.35))
                continue
            msg = await websocket.receive()
            if msg == "ping":
                await websocket.send("pong")
    finally:
        async with state.lock:
            state.viewers.discard(id(websocket))


@bp.websocket("/broadcast")
async def broadcast_ws():
    try:
        async with state.lock:
            state.broadcaster = id(websocket)
            queued = list(state.ice_queue)
            state.ice_queue.clear()
        for item in queued:
            await websocket.send(json.dumps({"type": "ice", "candidate": item}))
        while True:
            msg = await websocket.receive()
            data = json.loads(msg)
            if data.get("type") == "ice":
                async with state.lock:
                    state.ice_queue.append(data.get("candidate"))
    finally:
        async with state.lock:
            if state.broadcaster == id(websocket):
                state.broadcaster = None
                state.ice_queue.clear()
