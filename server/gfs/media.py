from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename


@dataclass(frozen=True)
class LocationVideo:
    url: str
    recorded_at: str
    source: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class LocationMediaStore:
    def __init__(self, *, data_dir: Path, media_dir: Path) -> None:
        self.data_dir = data_dir
        self.media_dir = media_dir
        self.json_store_path = data_dir / "gfs_location_store.json"
        self.fishvid_csv = data_dir / "fishvidlist.csv"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def _read_store(self) -> dict[str, Any]:
        if not self.json_store_path.exists():
            return {"videos": {}, "reports": {}, "live": {}}
        try:
            raw = json.loads(self.json_store_path.read_text(encoding="utf-8"))
            return {
                "videos": dict(raw.get("videos") or {}),
                "reports": dict(raw.get("reports") or {}),
                "live": dict(raw.get("live") or {}),
            }
        except Exception:
            return {"videos": {}, "reports": {}, "live": {}}

    def _write_store(self, payload: dict[str, Any]) -> None:
        self.json_store_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def videos_for(self, location_id: str) -> list[LocationVideo]:
        items: list[LocationVideo] = []
        if self.fishvid_csv.exists():
            with self.fishvid_csv.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    row_loc = (row.get("location_id") or row.get("location") or "").strip()
                    if row_loc != location_id:
                        continue
                    url = (row.get("url") or row.get("video") or "").strip()
                    if not url:
                        continue
                    items.append(LocationVideo(url=url, recorded_at=(row.get("recorded_at") or "").strip() or _utc_now_iso(), source="fishvidlist.csv"))
        store = self._read_store()
        for entry in store["videos"].get(location_id, []):
            url = str(entry.get("url") or "").strip()
            if not url:
                continue
            items.append(LocationVideo(url=url, recorded_at=str(entry.get("recorded_at") or _utc_now_iso()), source=str(entry.get("source") or "upload")))
        items.sort(key=lambda v: v.recorded_at, reverse=True)
        return items

    def reports_for(self, location_id: str) -> list[str]:
        store = self._read_store()
        return [str(x) for x in store["reports"].get(location_id, []) if str(x).strip()]

    def append_report(self, location_id: str, text: str) -> None:
        store = self._read_store()
        store["reports"].setdefault(location_id, []).append(text.strip())
        self._write_store(store)

    def set_live(self, location_id: str, active: bool, stream_url: str = "") -> dict[str, Any]:
        store = self._read_store()
        store["live"][location_id] = {
            "active": bool(active),
            "stream_url": stream_url.strip(),
            "updated_at": _utc_now_iso(),
        }
        self._write_store(store)
        return store["live"][location_id]

    def live_for(self, location_id: str) -> dict[str, Any]:
        store = self._read_store()
        return store["live"].get(location_id, {"active": False, "stream_url": "", "updated_at": None})

    def save_upload(self, *, location_id: str, filename: str, raw: bytes) -> dict[str, Any]:
        safe = secure_filename(filename) or "upload.mp4"
        target = self.media_dir / f"{location_id}-{int(datetime.now().timestamp())}-{safe}"
        target.write_bytes(raw)
        rel_url = f"/static/fishvid/{target.name}"
        store = self._read_store()
        store["videos"].setdefault(location_id, []).append(
            {"url": rel_url, "recorded_at": _utc_now_iso(), "source": "upload"}
        )
        self._write_store(store)
        return {"url": rel_url, "filename": target.name}
