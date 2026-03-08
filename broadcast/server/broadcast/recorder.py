from __future__ import annotations

from pathlib import Path


class Recorder:
    def start(self, output_path: Path) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b'')

    def stop(self, output_path: Path) -> None:
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.write_text('placeholder recording data', encoding='utf-8')
