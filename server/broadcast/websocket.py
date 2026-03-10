from __future__ import annotations

import asyncio


class BroadcastChatHub:
    def __init__(self) -> None:
        self.chat_clients: set = set()
        self.broadcast_clients: set = set()

    async def publish_chat(self, payload: dict) -> None:
        dead = []
        msg = payload
        for ws in list(self.chat_clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.chat_clients.discard(ws)

    async def publish_broadcast(self, payload: dict) -> None:
        dead = []
        for ws in list(self.broadcast_clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.broadcast_clients.discard(ws)


hub = BroadcastChatHub()
