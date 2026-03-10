from __future__ import annotations

from typing import Any


def build_cloud_clusters(lat, lon, cloud_fraction, humidity, z: int) -> list[dict[str, Any]]:
    """Create LOD cloud polygons with vertical profile metadata."""
    if cloud_fraction is None:
        return []
    stride = 12 if z < 3 else 6 if z < 6 else 3
    out: list[dict[str, Any]] = []
    h = cloud_fraction.shape[0]
    w = cloud_fraction.shape[1]
    for y in range(0, h - 1, stride):
        for x in range(0, w - 1, stride):
            cf = float(cloud_fraction[y, x])
            if cf < 0.15:
                continue
            rh = float(humidity[y, x]) if humidity is not None else 50.0
            ctype = "cirrus" if cf < 0.35 else "cumulus" if cf < 0.7 else "cumulonimbus"
            if ctype == "cirrus":
                base, top = 8000, 12000
            elif ctype == "cumulonimbus":
                base, top = 2000, 12000
            else:
                base, top = 1500, 5000
            out.append({
                "id": f"cloud-{y}-{x}",
                "lat": float(lat[y, x]),
                "lon": float(lon[y, x]),
                "cloud_type": ctype,
                "cloud_base": base,
                "cloud_top": top,
                "density": max(0.0, min(1.0, cf)),
                "convective_intensity": max(0.0, min(1.0, rh / 100.0)),
                "layers": 5 if z < 4 else 10 if z < 7 else 16,
            })
    return out
