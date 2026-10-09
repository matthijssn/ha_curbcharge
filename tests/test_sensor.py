"""Tests for CurbCharge sensor values and attributes."""

from dataclasses import replace
from decimal import Decimal

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.const import DOMAIN
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.models import ChargingTariff
from custom_components.curbcharge.sensor import (
    AvailableConnectorsSensor,
    BestNearbyChargerSensor,
    OccupiedConnectorsSensor,
    PricePerKwhSensor,
    StatusSummarySensor,
    UnavailableConnectorsSensor,
)
from tests.conftest import FakeProvider


def _coordinator(hass, station):
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    return coordinator


def test_connector_sensors_do_not_infer_occupancy(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)

    assert AvailableConnectorsSensor(coordinator, station).native_value == 2
    assert UnavailableConnectorsSensor(coordinator, station).native_value == 2
    assert OccupiedConnectorsSensor(coordinator, station).native_value is None


def test_price_sensor_uses_decimal_tariff_and_currency_unit(hass, sample_stations):
    tariff = ChargingTariff("EUR", Decimal("0.257"), None, None, None, "NDW DOT-NL")
    station = replace(sample_stations[0], tariff=tariff)
    coordinator = _coordinator(hass, station)
    sensor = PricePerKwhSensor(coordinator, station)

    assert sensor.native_value == 0.257
    assert sensor.native_unit_of_measurement == "EUR/kWh"


def test_price_sensor_is_unknown_without_tariff(hass, sample_stations):
    station = sample_stations[0]
    sensor = PricePerKwhSensor(_coordinator(hass, station), station)

    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement is None


def test_status_summary_exposes_operational_and_provider_status(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    sensor = StatusSummarySensor(coordinator, station)

    assert sensor.native_value == "available"
    assert sensor.extra_state_attributes["available_connectors"] == 2
    assert sensor.extra_state_attributes["occupied_connectors"] is None
    assert sensor.extra_state_attributes["unknown_connectors"] == 2
    assert sensor.extra_state_attributes["reserved_connectors"] is None
    assert sensor.extra_state_attributes["is_open"] is True
    assert sensor.extra_state_attributes["latitude"] == 52.0
    assert sensor.extra_state_attributes["address"] == "Teststraat 1"


def test_best_nearby_sensor_exposes_selection_reason(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hashed-area")
    sensor = BestNearbyChargerSensor(coordinator, entry)

    assert sensor.native_value == station.name
    assert sensor.extra_state_attributes["station_id"] == station.provider_id
    assert sensor.extra_state_attributes["available_connectors"] == 2
    assert sensor.extra_state_attributes["reason"] == "only_available_station"
