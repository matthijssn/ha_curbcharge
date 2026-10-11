"""Geographic helpers shared by country providers."""

import math

_EARTH_RADIUS_M = 6_371_000


def distance_m(
    latitude: float, longitude: float, other_latitude: float, other_longitude: float
) -> int:
    """Calculate surface distance with the haversine formula."""
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(other_longitude - longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(haversine)))
