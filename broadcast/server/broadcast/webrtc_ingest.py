from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from .recorder import Recorder
from .video_archive import VideoArchive


@dataclass
class ActiveStream:
    stream_id: str
    lat: float
    lng: float
    output_path: Path


class WebRTCIngest:
    def __init__(self, archive: VideoArchive) -> None:
        self.archive = archive
        self.recorder = Recorder()
        self.active: Dict[str, ActiveStream] = {}

    def start_stream(self, stream_id: str, lat: float, lng: float) -> ActiveStream:
        output_path = self.archive.create_clip_path(lat, lng)
        self.recorder.start(output_path)
        stream = ActiveStream(stream_id=stream_id, lat=lat, lng=lng, output_path=output_path)
        self.active[stream_id] = stream
        return stream

    def stop_stream(self, stream_id: str) -> Optional[ActiveStream]:
        stream = self.active.pop(stream_id, None)
        if stream:
            self.recorder.stop(stream.output_path)
        return stream
