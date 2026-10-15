"""Shared CurbCharge test fixtures."""

import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from custom_components.curbcharge.api import _station_from_feature
from custom_components.curbcharge.models import (
    FavouriteStationReference,
    StationDiscoveryResult,
)

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture
def sample_geojson() -> dict[str, Any]:
    """Load a sanitized example matching the documented DOT-NL schema."""
    fixture_path = Path(__file__).parent / "fixtures" / "stations.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def sample_stations(sample_geojson: dict[str, Any]):
    """Parse fixture stations at the configured test location."""
    return tuple(
        _station_from_feature(feature, 52.0, 5.0)
        for feature in sample_geojson["features"]
    )


class FakeProvider:
    """In-memory provider returning queued snapshots or failures."""

    provider_id = "ndw_dot_nl"
    provider_name = "NDW DOT-NL"
    country_code = "NL"

    def __init__(self, snapshots: list[Any] | None = None) -> None:
        self.snapshots = deque(snapshots or [])
        self.current: tuple[Any, ...] = ()
        self.validation_calls: list[tuple[float, float, int]] = []
        self.station_calls: list[
            tuple[float, float, int, int, tuple[FavouriteStationReference, ...]]
        ] = []
        self.validation_error: Exception | None = None

    async def async_get_stations(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_stations: int,
        favourites: tuple[FavouriteStationReference, ...] = (),
    ):
        self.station_calls.append(
            (latitude, longitude, radius_m, max_stations, favourites)
        )
        if self.snapshots:
            result = self.snapshots.popleft()
            if isinstance(result, Exception):
                raise result
            self.current = tuple(result)
        nearby = sorted(
            (
                station
                for station in self.current
                if station.distance_m is not None and station.distance_m <= radius_m
            ),
            key=lambda station: (station.distance_m, station.provider_id),
        )[:max_stations]
        nearby_ids = {station.provider_id for station in nearby}
        favourite_ids = {favourite.provider_id for favourite in favourites}
        return StationDiscoveryResult(
            nearby=nearby,
            favourites=tuple(
                station
                for station in self.current
                if station.provider_id in favourite_ids
                and station.provider_id not in nearby_ids
            ),
        )

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        self.validation_calls.append((latitude, longitude, radius_m))
        if self.validation_error is not None:
            raise self.validation_error
