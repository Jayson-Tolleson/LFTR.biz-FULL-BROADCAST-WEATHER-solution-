from __future__ import annotations


class GFSHub:
    def __init__(self) -> None:
        self.clients: set = set()

    async def broadcast(self, payload: dict) -> None:
        stale = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.clients.discard(ws)


hub = GFSHub()
