from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def build_live_session_payload(location_id: str) -> dict[str, Any]:
    """Return a real scaffold payload for browser WebRTC capture workflow."""
    return {
        "location_id": location_id,
        "capture": {"video": True, "audio": True},
        "overlay": {"width": 240, "height": 240},
        "recording": {"upload_route": f"/gfs/api/location/{location_id}/upload", "enabled": True},
        "status": "ready",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "todo": "Server-side SFU/relay negotiation can be attached later; browser capture+record upload flow is live.",
    }


def control_event(event_type: str, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"type": event_type, "detail": detail or {}}
