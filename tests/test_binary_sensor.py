"""Tests for the station availability binary sensor."""

from custom_components.curbcharge.binary_sensor import StationAvailableBinarySensor
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from tests.conftest import FakeProvider


def test_available_binary_sensor_tracks_reported_count(hass, sample_stations):
    station = sample_stations[0]
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entity = StationAvailableBinarySensor(coordinator, station)

    assert entity.is_on is True


def test_available_binary_sensor_does_not_assume_missing_status(hass, sample_stations):
    station = sample_stations[2]
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entity = StationAvailableBinarySensor(coordinator, station)

    assert entity.is_on is None


def test_available_binary_sensor_turns_off_when_no_connector_is_available(
    hass, sample_stations
):
    station = sample_stations[1]
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entity = StationAvailableBinarySensor(coordinator, station)

    assert entity.is_on is False
