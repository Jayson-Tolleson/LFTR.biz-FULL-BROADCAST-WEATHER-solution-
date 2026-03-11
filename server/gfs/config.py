from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GfsConfig:
    """Runtime configuration for the GFS weather engine."""

    thredds_best_url: str
    max_cells: int = 250_000
    default_pad: float = 0.10
    max_pad: float = 0.5
    debug_enabled: bool = False


def load_gfs_config(debug_enabled: bool = False) -> GfsConfig:
    return GfsConfig(
        thredds_best_url=os.getenv(
            "GFS_THREDDS_BEST_URL",
            "https://thredds.ucar.edu/thredds/dodsC/grib/NCEP/GFS/Global_0p25deg/Best",
        ),
        max_cells=int(os.getenv("GFS_MAX_CELLS", "250000")),
        default_pad=float(os.getenv("GFS_DEFAULT_PAD", "0.10")),
        max_pad=float(os.getenv("GFS_MAX_PAD", "0.50")),
        debug_enabled=debug_enabled,
    )
