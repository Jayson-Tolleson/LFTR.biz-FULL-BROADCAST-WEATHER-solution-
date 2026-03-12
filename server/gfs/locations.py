from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FishLocation:
    location_id: str
    name: str
    lat: float
    lon: float
    reports: list[str]


_slug_re = re.compile(r"[^a-z0-9]+")


def _slug(text: str) -> str:
    clean = _slug_re.sub("-", text.strip().lower()).strip("-")
    return clean[:60] if clean else "loc"


def _stable_location_id(name: str, lat: float, lon: float) -> str:
    slug = _slug(name)
    key = f"{slug}|{lat:.6f}|{lon:.6f}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return f"{slug}-{digest}"


def load_fish_locations(csv_path: Path) -> list[FishLocation]:
    if not csv_path.exists():
        return []
    rows: list[FishLocation] = []
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            try:
                lat = float((row.get("lat") or "").strip())
                lon = float((row.get("lon") or "").strip())
            except Exception:
                continue
            if lat < -90 or lat > 90 or lon < -180 or lon > 180:
                continue
            name = (row.get("name") or row.get("location") or "").strip() or f"Fish Spot {idx + 1}"
            reports = [
                (value or "").strip()
                for key, value in row.items()
                if key and key.startswith("report_") and (value or "").strip()
            ]
            location_id = _stable_location_id(name, lat, lon)
            rows.append(FishLocation(location_id=location_id, name=name, lat=lat, lon=lon, reports=reports))
    return rows


def location_to_json(loc: FishLocation) -> dict[str, Any]:
    latest_report = loc.reports[-1] if loc.reports else ""
    return {
        "id": loc.location_id,
        "name": loc.name,
        "lat": loc.lat,
        "lon": loc.lon,
        "last_report": latest_report,
        "all_reports": loc.reports,
    }
