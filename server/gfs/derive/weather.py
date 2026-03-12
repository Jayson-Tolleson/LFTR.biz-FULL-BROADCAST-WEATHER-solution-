from __future__ import annotations

from typing import Any


def derive_weather_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "wind_u": raw.get("wind_u", raw.get("u-component_of_wind_height_above_ground", [])),
        "wind_v": raw.get("wind_v", raw.get("v-component_of_wind_height_above_ground", [])),
        "gust": raw.get("wind_gust", raw.get("Wind_speed_gust_surface", [])),
        "mslp": raw.get("pressure_msl", raw.get("Pressure_reduced_to_MSL_msl", [])),
        "temp2m": raw.get("air_temp", raw.get("Temperature_height_above_ground", [])),
        "dewpoint2m": raw.get("dewpoint", raw.get("Dewpoint_temperature_height_above_ground", [])),
        "prate": raw.get("precip_rate", raw.get("Precipitation_rate_surface", [])),
        "cloud_cover": raw.get("cloud_total", raw.get("Total_cloud_cover_entire_atmosphere", [])),
    }
