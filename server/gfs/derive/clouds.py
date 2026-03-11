from __future__ import annotations

from typing import Any


def derive_cloud_layers(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    layers = [
        {"name": "low", "base_m": 500, "top_m": 2200, "density": raw.get("Low_cloud_cover_low_cloud", [])},
        {"name": "mid", "base_m": 2500, "top_m": 6000, "density": raw.get("Medium_cloud_cover_middle_cloud", [])},
        {"name": "high", "base_m": 7000, "top_m": 12000, "density": raw.get("High_cloud_cover_high_cloud", [])},
    ]
    convective = {
        "reflectivity": raw.get("Composite_reflectivity_entire_atmosphere", []),
        "lift": raw.get("Vertical_velocity_pressure_isobaric", []),
    }
    return layers, convective
