"""Shared CurbCharge test fixtures."""

import json
from collections import deque
from pathlib import Path
from typing import Any

import pytest

from custom_components.curbcharge.api import _station_from_feature

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

    def __init__(self, snapshots: list[Any] | None = None) -> None:
        self.snapshots = deque(snapshots or [])
        self.current: tuple[Any, ...] = ()
        self.validation_calls: list[tuple[float, float, int]] = []
        self.validation_error: Exception | None = None

    async def async_get_stations(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_stations: int,
    ):
        if self.snapshots:
            result = self.snapshots.popleft()
            if isinstance(result, Exception):
                raise result
            self.current = tuple(result)
        return self.current[:max_stations]

    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        self.validation_calls.append((latitude, longitude, radius_m))
        if self.validation_error is not None:
            raise self.validation_error
