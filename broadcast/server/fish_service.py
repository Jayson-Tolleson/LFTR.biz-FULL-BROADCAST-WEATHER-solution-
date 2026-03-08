from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent
FISH_CSV = BASE_DIR / "data" / "fishloclist.csv"


class FishService:
    def load_fish_locations(self) -> List[Dict[str, Any]]:
        if not FISH_CSV.exists():
            return []

        items: List[Dict[str, Any]] = []
        with FISH_CSV.open("r", encoding="utf-8") as file_obj:
            reader = csv.DictReader(file_obj)
            for row in reader:
                items.append(
                    {
                        "name": row.get("name", "Unknown"),
                        "lat": float(row.get("lat", 0.0)),
                        "lng": float(row.get("lng", 0.0)),
                        "video_url": row.get("video_url", ""),
                    }
                )
        return items
