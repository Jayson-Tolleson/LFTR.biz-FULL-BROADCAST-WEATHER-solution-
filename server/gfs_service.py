import os
from dataclasses import dataclass


def shape_of(field):
    rows = len(field)
    cols = len(field[0]) if rows else 0
    return (rows, cols)


@dataclass
class GridMismatchError(Exception):
    field_name: str
    field_shape: tuple
    target_shape: tuple

    def __str__(self):
        return f"grid mismatch for {self.field_name}: {self.field_shape} != {self.target_shape}"


def ensure_same_grid(field, target_shape, field_name: str):
    shape = shape_of(field)
    if shape != tuple(target_shape):
        raise GridMismatchError(field_name=field_name, field_shape=shape, target_shape=tuple(target_shape))
    return field


def generate_live_payload(cloud, precip, *, allow_synthetic_fallback: bool = False):
    canonical_shape = shape_of(cloud)
    diagnostics = {"data_source": "live_gfs_0p25", "canonical_shape": list(canonical_shape), "used_fallback": False, "fallback_reason": None}
    try:
        ensure_same_grid(precip, canonical_shape, "precip")
        return {"ok": True, "diagnostics": diagnostics, "blend_shape": list(canonical_shape)}
    except GridMismatchError as err:
        if not allow_synthetic_fallback:
            return {
                "ok": False,
                "error": str(err),
                "diagnostics": {**diagnostics, "fallback_reason": str(err), "grid_shape_report": {"cloud": list(shape_of(cloud)), "precip": list(shape_of(precip))}},
            }
        return {"ok": True, "diagnostics": {**diagnostics, "data_source": "synthetic_fallback", "used_fallback": True, "fallback_reason": str(err)}}


def fallback_allowed() -> bool:
    return os.getenv("ALLOW_SYNTHETIC_FALLBACK", "false").lower() in {"1", "true", "yes"}
