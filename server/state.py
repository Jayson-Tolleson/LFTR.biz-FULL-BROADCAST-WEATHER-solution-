from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass
class RoomSettings:
    ai_enabled: bool = True
    tts_enabled: bool = True
    stt_enabled: bool = True


@dataclass
class RoomState:
    broadcaster_sid: Optional[str] = None
    viewers: Dict[str, Any] = field(default_factory=dict)
    settings: RoomSettings = field(default_factory=RoomSettings)


class AppState:
    def __init__(self, default_room: str = "default") -> None:
        self.default_room = default_room
        self.rooms: Dict[str, RoomState] = {}
        self.sid_meta: Dict[str, Tuple[str, str]] = {}
        self.sio = None

    def ensure_room(self, room_id: str) -> RoomState:
        room = self.rooms.get(room_id)
        if room is None:
            room = RoomState()
            self.rooms[room_id] = room
        return room

    def get_room(self, room_id: str) -> Optional[RoomState]:
        return self.rooms.get(room_id)

    def remove_room_if_empty(self, room_id: str) -> None:
        room = self.rooms.get(room_id)
        if not room:
            return
        if room.broadcaster_sid is None and not room.viewers:
            self.rooms.pop(room_id, None)

    def set_sid_meta(self, sid: str, room_id: str, role: str) -> None:
        self.sid_meta[sid] = (room_id, role)

    def get_sid_meta(self, sid: str) -> Tuple[str, str]:
        return self.sid_meta.get(sid, (self.default_room, "unknown"))

    def pop_sid_meta(self, sid: str) -> Tuple[str, str]:
        return self.sid_meta.pop(sid, (self.default_room, "unknown"))
