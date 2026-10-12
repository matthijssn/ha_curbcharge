"""Tests for CurbCharge sensor values and attributes."""

from dataclasses import replace
from decimal import Decimal

from homeassistant.const import EntityCategory
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge.const import DOMAIN
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.models import ChargingTariff, TariffStatus
from custom_components.curbcharge.sensor import (
    AvailableConnectorsSensor,
    BestNearbyChargerSensor,
    ConnectorGroupSensor,
    DistanceSensor,
    MaximumChargingPowerSensor,
    OccupiedConnectorsSensor,
    PricePerKwhSensor,
    StatusSummarySensor,
    TotalConnectorsSensor,
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

    available = AvailableConnectorsSensor(coordinator, station)
    assert available.native_value == 2
    assert available.entity_category is EntityCategory.DIAGNOSTIC
    assert UnavailableConnectorsSensor(coordinator, station).native_value == 2
    assert OccupiedConnectorsSensor(coordinator, station).native_value is None
    assert TotalConnectorsSensor(coordinator, station).native_value == 4
    assert MaximumChargingPowerSensor(coordinator, station).native_value == 50
    assert DistanceSensor(coordinator, station).native_value == 0


def test_connector_group_sensor_exposes_provider_group_counts(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    connector = station.connectors[0]
    sensor = ConnectorGroupSensor(coordinator, station, connector)

    assert sensor.native_value == connector.count
    assert sensor.extra_state_attributes["available_connectors"] == (
        connector.available_count
    )
    assert sensor.extra_state_attributes["connector_type"] == connector.connector_type
    assert sensor.entity_registry_enabled_default is False

    unknown_connector = replace(connector, connector_type=None)
    unknown_sensor = ConnectorGroupSensor(coordinator, station, unknown_connector)
    assert unknown_sensor.translation_placeholders["connector_type"] == "Unknown"


def test_price_sensor_uses_decimal_tariff_and_currency_unit(hass, sample_stations):
    tariff = ChargingTariff(
        "EUR",
        Decimal("0.257"),
        None,
        None,
        "Public tariff",
        "NDW DOT-NL",
        tariff_id="tariff-1",
        tariff_type="AD_HOC_PAYMENT",
        status=TariffStatus.AVAILABLE,
    )
    station = replace(
        sample_stations[0],
        connectors=(),
        tariff=tariff,
        tariff_status=TariffStatus.AVAILABLE,
    )
    coordinator = _coordinator(hass, station)
    sensor = PricePerKwhSensor(coordinator, station)

    assert sensor.native_value == 0.257
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    attributes = sensor.extra_state_attributes
    assert attributes["price_per_kwh"] == 0.257
    assert attributes["currency"] == "EUR"
    assert attributes["tariff_description"] == "Public tariff"
    assert attributes["tariff_source"] == "NDW DOT-NL"
    assert attributes["tariff_ids"] == ["tariff-1"]
    assert attributes["tariff_type"] == "AD_HOC_PAYMENT"
    assert attributes["tariff_status"] == "available"

    summary_attributes = StatusSummarySensor(
        coordinator, station
    ).extra_state_attributes
    assert {key: summary_attributes[key] for key in attributes} == attributes


def test_price_sensor_is_unknown_without_tariff(hass, sample_stations):
    station = sample_stations[0]
    sensor = PricePerKwhSensor(_coordinator(hass, station), station)

    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement is None

    unknown_price = ChargingTariff("EUR", None, None, None, None, "NDW DOT-NL")
    station = replace(station, tariff=unknown_price)
    sensor = PricePerKwhSensor(_coordinator(hass, station), station)
    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement == "EUR/kWh"
    assert sensor.extra_state_attributes["tariff_status"] == "incomplete"


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
    assert sensor.extra_state_attributes["station_name"] == station.name
    assert sensor.extra_state_attributes["total_connectors"] == station.total_connectors
    assert sensor.extra_state_attributes["distance_m"] == station.distance_m
    assert sensor.extra_state_attributes["max_power_kw"] == station.max_power_kw
    assert sensor.extra_state_attributes["tariff_status"] == "incomplete"
    tariff_groups = [
        group
        for group in sensor.extra_state_attributes["connector_tariffs"]
        if group["tariff_ids"]
    ]
    assert len(tariff_groups) == 1
    assert tariff_groups[0]["tariff_ids"] == ["tariff-eur-022"]


def test_summary_exposes_conflicting_connector_tariffs(hass, sample_stations):
    station = sample_stations[0]
    tariff_a = ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW DOT-NL")
    tariff_b = ChargingTariff("EUR", Decimal("0.3"), None, None, None, "NDW DOT-NL")
    connector_a = replace(
        station.connectors[0],
        tariff_ids=("tariff-a",),
        tariffs=(tariff_a,),
        tariff_status=TariffStatus.AVAILABLE,
    )
    connector_b = replace(
        station.connectors[1],
        tariff_ids=("tariff-b",),
        tariffs=(tariff_b,),
        tariff_status=TariffStatus.AVAILABLE,
    )
    station = replace(
        station,
        connectors=(connector_a, connector_b),
        tariff=None,
        tariff_status=TariffStatus.AMBIGUOUS,
    )
    coordinator = _coordinator(hass, station)

    summary_attributes = StatusSummarySensor(
        coordinator, station
    ).extra_state_attributes
    price_attributes = PricePerKwhSensor(coordinator, station).extra_state_attributes

    assert summary_attributes["price_per_kwh"] is None
    assert summary_attributes["tariff_status"] == "ambiguous"
    assert [
        group["tariffs"][0]["price_per_kwh"]
        for group in summary_attributes["connector_tariffs"]
    ] == [0.2, 0.3]
    assert price_attributes["price_per_kwh"] is None
    assert price_attributes["tariff_status"] == "ambiguous"


def test_best_nearby_sensor_exposes_selection_reason(hass, sample_stations):
    station = sample_stations[0]
    coordinator = _coordinator(hass, station)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="hashed-area")
    sensor = BestNearbyChargerSensor(coordinator, entry)

    assert sensor.native_value == station.name
    assert sensor.extra_state_attributes["station_id"] == station.provider_id
    assert sensor.extra_state_attributes["available_connectors"] == 2
    assert sensor.extra_state_attributes["reason"] == "only_available_station"


def test_entities_are_unavailable_without_current_station_data(hass, sample_stations):
    station = sample_stations[0]
    stale_coordinator = _coordinator(hass, replace(station, is_stale=True))
    assert not AvailableConnectorsSensor(stale_coordinator, station).available

    coordinator = _coordinator(hass, station)
    connector_sensor = AvailableConnectorsSensor(coordinator, station)
    assert connector_sensor.available

    coordinator.async_set_updated_data({})
    assert not connector_sensor.available
    assert StatusSummarySensor(coordinator, station).native_value == "unknown"
    assert StatusSummarySensor(coordinator, station).extra_state_attributes == {
        "provider_station_id": station.provider_id
    }

    entry = MockConfigEntry(domain=DOMAIN, unique_id="hashed-area")
    best_sensor = BestNearbyChargerSensor(coordinator, entry)
    assert best_sensor.native_value is None
    assert best_sensor.extra_state_attributes["reason"] == "no_available_station"
