"""Sensors for CurbCharge stations and recommendations."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import CurbChargeCoordinator
from .entity import CurbChargeStationEntity, setup_dynamic_station_entities
from .models import ChargingConnector, ChargingStation, ChargingTariff, TariffStatus

PARALLEL_UPDATES = 0


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
    async_add_entities(
        [
            BestNearbyChargerSensor(coordinator, entry),
            NearbyChargingUnitsSensor(coordinator, entry),
        ]
    )


class AvailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors the provider reports as available."""

    _attr_translation_key = "available_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.available_connectors


class OccupiedConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Occupied count only when the provider makes it knowable."""

    _attr_translation_key = "occupied_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.occupied_connectors


class UnavailableConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Number of connectors not reported as available."""

    _attr_translation_key = "unavailable_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.unavailable_connectors


class TotalConnectorsSensor(CurbChargeStationEntity, SensorEntity):
    """Total number of connectors in the provider's availability report."""

    _attr_translation_key = "total_connectors"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        station = self.station
        return None if station is None else station.total_connectors


class ConnectorGroupSensor(CurbChargeStationEntity, SensorEntity):
    """Expose one provider-reported group of equivalent connectors."""

    _attr_translation_key = "connector_group"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: CurbChargeCoordinator,
        station: ChargingStation,
        connector: ChargingConnector,
    ) -> None:
        """Initialize a connector group without implying individual plug IDs."""
        group_index = connector.provider_connector_id.rsplit(":", 1)[-1]
        super().__init__(
            coordinator, station, unique_id_suffix=f"connector_group_{group_index}"
        )
        self._connector = connector
        self._attr_translation_placeholders = {
            "connector_type": _display_connector_type(connector.connector_type)
        }

    @property
    def native_value(self) -> int:
        """Return the provider-reported total in this connector group."""
        return self._connector.count

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only counts and properties actually present in the feed."""
        available = self._connector.available_count
        return {
            "available_connectors": available,
            "unavailable_connectors": (
                self._connector.count - available if available is not None else None
            ),
            "occupied_connectors": (0 if available == self._connector.count else None),
            "connector_type": self._connector.connector_type,
            "connector_format": self._connector.connector_format,
            "power_type": self._connector.power_type,
            "max_power_kw": self._connector.max_power_kw,
        }


def _display_connector_type(connector_type: str | None) -> str:
    if connector_type is None:
        return "Unknown"
    return connector_type.replace("_", " ").title()


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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station = self.station
        return {} if station is None else _tariff_attributes(station)


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
            return {"provider_station_id": self._station_id}
        attributes = {
            "station_name": station.name,
            "available_connectors": station.available_connectors,
            "occupied_connectors": station.occupied_connectors,
            "unavailable_connectors": station.unavailable_connectors,
            "total_connectors": station.total_connectors,
            "reserved_connectors": station.reserved_connectors,
            "out_of_service_connectors": station.out_of_service_connectors,
            "unknown_connectors": station.unknown_connectors,
            "connector_types": list(station.connector_types),
            "operator": station.operator,
            "address": station.address,
            "distance_m": station.distance_m,
            "max_power_kw": station.max_power_kw,
            **_tariff_attributes(station),
            "latitude": station.latitude,
            "longitude": station.longitude,
            "provider": station.provider,
            "country_code": station.country_code,
            "provider_station_id": station.provider_id,
            "last_updated": _isoformat(station.last_updated),
            "last_successful_update": _isoformat(
                self.coordinator.last_successful_update
            ),
            "is_open": station.is_open,
            "availability_reported": station.availability_reported,
            "evse_status": (
                station.evse_status.value if station.evse_status is not None else None
            ),
        }
        if station.tariff is None and any(
            connector.tariff_ids for connector in station.connectors
        ):
            attributes["connector_tariffs"] = [
                _connector_tariff_attributes(connector)
                for connector in station.connectors
            ]
        return attributes


def _tariff_attributes(station: ChargingStation) -> dict[str, Any]:
    """Return the station-level CPO tariff in a stable sensor attribute shape."""
    tariff = station.tariff
    tariff_ids = {
        tariff_id
        for connector in station.connectors
        for tariff_id in connector.tariff_ids
    }
    if tariff is not None and tariff.tariff_id is not None:
        tariff_ids.add(tariff.tariff_id)
    return {
        "price_per_kwh": _decimal_attribute(
            tariff.price_per_kwh if tariff is not None else None
        ),
        "currency": tariff.currency if tariff is not None else None,
        "tariff_description": tariff.description if tariff is not None else None,
        "tariff_source": tariff.source if tariff is not None else None,
        "tariff_vat_percentage": _decimal_attribute(
            tariff.vat_percentage if tariff is not None else None
        ),
        "session_fee": _decimal_attribute(
            tariff.session_fee if tariff is not None else None
        ),
        "price_per_minute": _decimal_attribute(
            tariff.price_per_minute if tariff is not None else None
        ),
        "tariff_ids": sorted(tariff_ids),
        "tariff_type": tariff.tariff_type if tariff is not None else None,
        "tariff_status": _station_tariff_status(station).value,
    }


def _station_tariff_status(station: ChargingStation) -> TariffStatus:
    if station.tariff_status is not None:
        return station.tariff_status
    if station.tariff is not None:
        return station.tariff.effective_status
    if any(connector.tariff_ids for connector in station.connectors):
        return TariffStatus.INCOMPLETE
    return TariffStatus.NOT_REPORTED


def _connector_tariff_attributes(connector: ChargingConnector) -> dict[str, Any]:
    status = connector.tariff_status or (
        TariffStatus.INCOMPLETE if connector.tariff_ids else TariffStatus.NOT_REPORTED
    )
    return {
        "connector_type": connector.connector_type,
        "connector_format": connector.connector_format,
        "power_type": connector.power_type,
        "max_power_kw": connector.max_power_kw,
        "count": connector.count,
        "tariff_ids": list(connector.tariff_ids),
        "tariff_status": status.value,
        "tariffs": [
            {
                **_tariff_record_attributes(tariff),
                "tariff_id": tariff.tariff_id,
            }
            for tariff in connector.tariffs
        ],
    }


def _tariff_record_attributes(tariff: ChargingTariff) -> dict[str, Any]:
    return {
        "price_per_kwh": _decimal_attribute(tariff.price_per_kwh),
        "currency": tariff.currency,
        "tariff_description": tariff.description,
        "tariff_source": tariff.source,
        "tariff_vat_percentage": _decimal_attribute(tariff.vat_percentage),
        "session_fee": _decimal_attribute(tariff.session_fee),
        "price_per_minute": _decimal_attribute(tariff.price_per_minute),
        "tariff_type": tariff.tariff_type,
        "tariff_status": tariff.effective_status.value,
    }


def _decimal_attribute(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


class NearbyChargingUnitsSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """Count current provider charging units in the configured search area."""

    _attr_has_entity_name = True
    _attr_translation_key = "nearby_charging_units"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CurbChargeCoordinator, entry: ConfigEntry[Any]
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{entry.unique_id or entry.entry_id}_nearby_charging_units"
        )

    @property
    def native_value(self) -> int | None:
        stations = self.coordinator.data
        if stations is None:
            return None
        return sum(not station.is_stale for station in stations.values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        stations = self.coordinator.data
        current_stations = (
            ()
            if stations is None
            else tuple(station for station in stations.values() if not station.is_stale)
        )
        stale_station_count = (
            None if stations is None else len(stations) - len(current_stations)
        )
        if stations is None:
            results_may_be_truncated = None
        elif len(current_stations) >= self.coordinator.max_stations:
            results_may_be_truncated = True
        elif stale_station_count == 0 and self.coordinator.include_unknown:
            results_may_be_truncated = False
        else:
            results_may_be_truncated = None

        return {
            "provider": self.coordinator.provider_name,
            "provider_id": self.coordinator.provider_id,
            "country_code": self.coordinator.country_code,
            "search_radius_m": self.coordinator.radius_m,
            "max_stations": self.coordinator.max_stations,
            "counted_unit": "charging_station",
            "include_unknown": self.coordinator.include_unknown,
            "unknown_availability_count": (
                None
                if stations is None
                else sum(
                    station.has_available_connectors is None
                    for station in current_stations
                )
            ),
            "stale_station_count": stale_station_count,
            "results_may_be_truncated": results_may_be_truncated,
            "last_successful_update": _isoformat(
                self.coordinator.last_successful_update
            ),
        }


class BestNearbyChargerSensor(CoordinatorEntity[CurbChargeCoordinator], SensorEntity):
    """The best currently available station under the configured ranking."""

    _attr_has_entity_name = True
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
            "provider": station.provider,
            "country_code": station.country_code,
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
        *(
            ConnectorGroupSensor(coordinator, station, connector)
            for connector in station.connectors
        ),
    ]


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
