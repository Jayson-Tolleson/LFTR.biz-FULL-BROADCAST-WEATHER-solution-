from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Dict, List

from .feature_engineering import build_feature_vector
from .model_loader import load_model, save_model
from .training_data import load_training_events


class BaitPredictor:
    def __init__(self, model_path: Path, fish_csv: Path, report_events: Path) -> None:
        self.model_path = model_path
        self.fish_csv = fish_csv
        self.report_events = report_events
        self.model: Dict = load_model(model_path)

    def needs_retrain(self) -> bool:
        last = float(self.model.get('trained_at', 0.0))
        return (time.time() - last) > 86400 or not self.model

    def train_if_needed(self) -> None:
        if not self.needs_retrain():
            return
        events = load_training_events(self.fish_csv, self.report_events)
        positive = sum(1 for e in events if e['bait_presence'] == 1)
        negative = max(1, len(events) - positive)
        self.model = {
            'trained_at': time.time(),
            'bias': math.log((positive + 1) / (negative + 1)),
            'weight_chl': 1.7,
            'weight_temp': 1.1,
            'weight_predator': 1.4,
            'weight_reports': 1.8,
            'events': len(events),
        }
        save_model(self.model_path, self.model)

    def predict_probability(self, feature_cell: Dict) -> float:
        self.train_if_needed()
        fv = build_feature_vector(feature_cell)
        z = (
            self.model.get('bias', 0)
            + fv['chlorophyll'] * self.model.get('weight_chl', 1.7)
            + fv['temp_optimality'] * self.model.get('weight_temp', 1.1)
            + fv['predator_signal'] * self.model.get('weight_predator', 1.4)
            + fv['report_density'] * self.model.get('weight_reports', 1.8)
            - (fv['distance_to_coast'] / 200)
        )
        return 1 / (1 + math.exp(-z))

    def forecast_cells(self, cells: List[Dict]) -> Dict[str, List[Dict]]:
        horizons = {'now': 1.0, 'plus_6h': 1.03, 'plus_24h': 0.94}
        out: Dict[str, List[Dict]] = {}
        for key, scale in horizons.items():
            res = []
            for c in cells:
                prob = min(1.0, max(0.0, self.predict_probability(c) * scale))
                res.append({'lat': c['lat'], 'lng': c['lng'], 'probability': round(prob, 3)})
            out[key] = res
        return out
