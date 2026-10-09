"""Sensors for CurbCharge stations and recommendations."""

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import CurbChargeCoordinator
from .entity import CurbChargeStationEntity, setup_dynamic_station_entities
from .models import ChargingStation


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CurbChargeCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up station and integration-level sensors."""
    coordinator = entry.runtime_data
    setup_dynamic_station_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda station: create_station_sensors(coordinator, station),
    )
    async_add_entities([BestNearbyChargerSensor(coordinator, entry)])


class AvailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors the provider reports as available."""

    _attr_translation_key = "available_connectors"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.available_connectors


class OccupiedConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Occupied count only when the provider makes it knowable."""

    _attr_translation_key = "occupied_connectors"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.occupied_connectors


class UnavailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors not reported as available."""

    _attr_translation_key = "unavailable_connectors"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.unavailable_connectors


class TotalConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Total number of connectors in the provider's availability report."""

    _attr_translation_key = "total_connectors"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.total_connectors


class MaximumChargingPowerSensor(CurbChargeStationEntity, SensorEntity):
    """Highest reported charging power."""

    _attr_translation_key = "maximum_charging_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        station = self.station
        return None if station is None else station.max_power_kw


class PricePerKwhSensor(CurbChargeStationEntity, SensorEntity):
    """Provider-reported per-kWh price when unambiguous."""

    _attr_translation_key = "price_per_kwh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_unit_of_measurement(self) -> str | None:
        station = self.station
        if station is None or station.tariff is None:
            return None
        return f"{station.tariff.currency}/kWh"

    @property
    def native_value(self) -> float | None:
        station = self.station
        if (
            station is None
            or station.tariff is None
            or station.tariff.price_per_kwh is None
        ):
            return None
        return float(station.tariff.price_per_kwh)


class DistanceSensor(CurbChargeStationEntity, SensorEntity):
    """Distance to the configured search location."""

    _attr_translation_key = "distance"
    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.METERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.distance_m


class StatusSummarySensor(CurbChargeStationEntity, SensorEntity):
    """Localized station availability summary and status attributes."""

    _attr_translation_key = "status_summary"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["available", "none_available", "unknown"]

    @property
    def native_value(self) -> str:
        station = self.station
        if station is None or station.has_available_connectors is None:
            return "unknown"
        return "available" if station.has_available_connectors else "none_available"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        if station is None:
            return {}
        return {
            "available_connectors": station.available_connectors,
            "occupied_connectors": station.occupied_connectors,
            "unavailable_connectors": station.unavailable_connectors,
            "reserved_connectors": station.reserved_connectors,
            "out_of_service_connectors": station.out_of_service_connectors,
            "unknown_connectors": station.unknown_connectors,
            "connector_types": list(station.connector_types),
            "operator": station.operator,
            "address": station.address,
            "tariff_description": (
                station.tariff.description if station.tariff else None
            ),
            "tariff_source": station.tariff.source if station.tariff else None,
            "tariff_vat_percentage": (
                float(station.tariff.vat_percentage)
                if station.tariff and station.tariff.vat_percentage is not None
                else None
            ),
            "latitude": station.latitude,
            "longitude": station.longitude,
            "provider": station.provider,
            "provider_station_id": station.provider_id,
            "last_updated": _isoformat(station.last_updated),
            "last_successful_update": _isoformat(
                self.coordinator.last_successful_update
            ),
            "is_open": station.is_open,
            "availability_reported": station.availability_reported,
        }


class BestNearbyChargerSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """The best currently available station under the configured ranking."""

    _attr_has_entity_name = False
    _attr_translation_key = "best_nearby_charger"

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{entry.unique_id or entry.entry_id}_best_nearby_charger"
        )

    @property
    def native_value(self) -> str | None:
        station = self.coordinator.best_station
        return None if station is None else station.name

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.coordinator.best_station
        if station is None:
            return {
                "reason": "no_available_station",
                "last_updated": _isoformat(self.coordinator.last_successful_update),
            }
        tariff = station.tariff
        return {
            "station_id": station.provider_id,
            "distance_m": station.distance_m,
            "available_connectors": station.available_connectors,
            "max_power_kw": station.max_power_kw,
            "price_per_kwh": (
                float(tariff.price_per_kwh)
                if tariff is not None and tariff.price_per_kwh is not None
                else None
            ),
            "currency": tariff.currency if tariff else None,
            "vat_percentage": (
                float(tariff.vat_percentage)
                if tariff and tariff.vat_percentage is not None
                else None
            ),
            "reason": self.coordinator.best_station_reason,
            "last_updated": _isoformat(self.coordinator.last_successful_update),
        }


def create_station_sensors(
    coordinator: CurbChargeCoordinator, station: ChargingStation
) -> list[SensorEntity]:
    """Create the station's translated sensor entities."""
    return [
        AvailableConnectorsSensor(coordinator, station),
        OccupiedConnectorsSensor(coordinator, station),
        UnavailableConnectorsSensor(coordinator, station),
        TotalConnectorsSensor(coordinator, station),
        MaximumChargingPowerSensor(coordinator, station),
        PricePerKwhSensor(coordinator, station),
        DistanceSensor(coordinator, station),
        StatusSummarySensor(coordinator, station),
    ]


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
