import asyncio


class BroadcastState:
    def __init__(self):
        self.broadcaster = None
        self.viewers = set()
        self.ice_queue = []
        self.lock = asyncio.Lock()


state = BroadcastState()
