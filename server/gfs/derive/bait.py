from __future__ import annotations

from typing import Any


def derive_bait_payload(_atmospheric: dict[str, Any], _ocean: dict[str, Any], _bio: dict[str, Any]) -> dict[str, Any]:
    # TODO: wire SST/current/chlorophyll forcing into score model.
    return {
        "bait_score": [],
        "front_lines": [],
        "convergence_polygons": [],
        "boil_probability_polygons": [],
        "confidence": {"atmospheric": 0.7, "ocean": 0.2, "bio": 0.1, "overall": 0.33},
    }
