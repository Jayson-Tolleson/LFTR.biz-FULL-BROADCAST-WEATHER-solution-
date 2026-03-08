from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List


def load_training_events(fishloc_csv: Path, report_events_file: Path) -> List[dict]:
    events: List[dict] = []
    if fishloc_csv.exists():
        with fishloc_csv.open('r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                events.append({'lat': float(row['lat']), 'lng': float(row['lng']), 'bait_presence': 1, 'timestamp': 'seed'})

    if report_events_file.exists():
        with report_events_file.open('r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                events.append({'lat': item['lat'], 'lng': item['lng'], 'bait_presence': int(item.get('bait_presence', 1)), 'timestamp': item.get('timestamp', '')})

    negatives: List[dict] = []
    for ev in events[: len(events) // 2]:
        negatives.append({'lat': ev['lat'] + 1.2, 'lng': ev['lng'] + 1.2, 'bait_presence': 0, 'timestamp': ev['timestamp']})

    return events + negatives
