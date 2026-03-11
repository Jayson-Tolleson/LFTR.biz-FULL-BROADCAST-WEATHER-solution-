from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

WEATHER_VARIABLES = (
    "u-component_of_wind_height_above_ground",
    "v-component_of_wind_height_above_ground",
    "Wind_speed_gust_surface",
    "Pressure_reduced_to_MSL_msl",
    "Temperature_height_above_ground",
    "Dewpoint_temperature_height_above_ground",
    "Precipitation_rate_surface",
    "Total_precipitation_surface_Mixed_intervals_Accumulation",
    "Downward_Short-Wave_Radiation_Flux_surface_Mixed_intervals_Average",
    "Total_cloud_cover_entire_atmosphere",
)

CLOUD_VARIABLES = (
    "Low_cloud_cover_low_cloud",
    "Medium_cloud_cover_middle_cloud",
    "High_cloud_cover_high_cloud",
    "Total_cloud_cover_entire_atmosphere",
    "Cloud_water_entire_atmosphere_single_layer",
    "Cloud_mixing_ratio_isobaric",
    "Ice_water_mixing_ratio_isobaric",
    "Rain_mixing_ratio_isobaric",
    "Snow_mixing_ratio_isobaric",
    "Relative_humidity_isobaric",
    "Temperature_isobaric",
    "Vertical_velocity_pressure_isobaric",
    "Composite_reflectivity_entire_atmosphere",
)

BAIT_ATMOSPHERIC_VARIABLES = (
    "u-component_of_wind_height_above_ground",
    "v-component_of_wind_height_above_ground",
    "Wind_speed_gust_surface",
    "Pressure_reduced_to_MSL_msl",
    "Total_cloud_cover_entire_atmosphere",
    "Downward_Short-Wave_Radiation_Flux_surface_Mixed_intervals_Average",
    "Precipitation_rate_surface",
)


@dataclass(frozen=True)
class BBox:
    west: float
    south: float
    east: float
    north: float

    def as_list(self) -> list[float]:
        return [self.west, self.south, self.east, self.north]


@dataclass(frozen=True)
class RequestIntent:
    bbox: BBox
    bboxes: tuple[BBox, ...]
    pad: float
    quality: str
    stride: int
    valid_time: datetime | None
    debug: bool = False


@dataclass
class EngineResult:
    payload: dict[str, Any]
    stale: bool = False
