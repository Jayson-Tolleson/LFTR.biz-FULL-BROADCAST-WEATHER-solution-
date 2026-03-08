from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List


class VideoArchive:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def location_dir(self, lat: float, lng: float) -> Path:
        path = self.root / f"{lat:.2f}_{lng:.2f}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def create_clip_path(self, lat: float, lng: float) -> Path:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H%M%S')
        return self.location_dir(lat, lng) / f'{ts}.mp4'

    def list_clips(self, lat: float, lng: float) -> List[str]:
        folder = self.location_dir(lat, lng)
        return sorted([p.name for p in folder.glob('*.mp4')], reverse=True)
