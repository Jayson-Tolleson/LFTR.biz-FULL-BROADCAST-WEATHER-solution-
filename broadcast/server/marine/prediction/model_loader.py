from __future__ import annotations

import json
from pathlib import Path
from typing import Dict


def save_model(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding='utf-8')


def load_model(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding='utf-8'))
