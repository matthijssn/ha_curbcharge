"""Shared entities and dynamic station lifecycle support."""

from collections.abc import Callable, Sequence
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import (
    DeviceEntry,
    DeviceInfo,
    DeviceRegistry,
)
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CurbChargeCoordinator
from .models import ChargingStation


class CurbChargeStationEntity(CoordinatorEntity[CurbChargeCoordinator]):
    """Base class for entities belonging to one charging station."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CurbChargeCoordinator,
        station: ChargingStation,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize an entity with stable station identity."""
        super().__init__(coordinator)
        self._station_id = station.provider_id
        entity_key = unique_id_suffix or self._attr_translation_key
        self._attr_unique_id = f"{station.provider}_{station.provider_id}_{entity_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={
                station_device_identifier(station.provider, station.provider_id)
            },
            name=f"{station.name} ({station.provider}:{station.provider_id})",
            manufacturer=station.operator,
            model=station.provider_name,
            serial_number=station.provider_id,
        )

    def attach_to_location_device(
        self,
        station: ChargingStation,
        parent_device: DeviceEntry,
        parent_identifier: tuple[str, str],
    ) -> None:
        """Register an EVSE as a child of its provider-reported location."""
        identifiers = {station_device_identifier(station.provider, station.provider_id)}
        name = f"{station.name} ({station.provider}:{station.provider_id})"
        child_device_info = getattr(dr, "ChildDeviceInfo", None)
        if child_device_info is not None:
            self._attr_device_info = child_device_info(
                identifiers=identifiers,
                name=name,
                parent_device_id=parent_device.id,
            )
            return

        self._attr_device_info = cast(
            DeviceInfo,
            {
                "identifiers": identifiers,
                "name": name,
                "manufacturer": station.operator,
                "model": station.provider_name,
                "serial_number": station.provider_id,
                "via_device": parent_identifier,
            },
        )

    @property
    def station(self) -> ChargingStation | None:
        """Return this station's latest coordinator data."""
        return (self.coordinator.data or {}).get(self._station_id)

    @property
    def available(self) -> bool:
        """Mark retained stations unavailable while they are absent upstream."""
        station = self.station
        return super().available and station is not None and not station.is_stale


def station_device_identifier(provider: str, station_id: str) -> tuple[str, str]:
    """Build a provider-scoped device identifier without location data."""
    return DOMAIN, f"{provider}:{station_id}"


def location_device_identifier(provider: str, location_id: str) -> tuple[str, str]:
    """Build a provider-scoped identifier for a distinct physical location."""
    return DOMAIN, f"{provider}:location:{location_id}"


def get_or_create_location_device(
    device_registry: DeviceRegistry,
    entry: ConfigEntry,
    station: ChargingStation,
) -> tuple[DeviceEntry, tuple[str, str]] | None:
    """Get the provider-reported location device for a station, if distinct."""
    location = station.location
    if (
        location is None
        or location.provider != station.provider
        or location.provider_id == station.provider_id
    ):
        return None

    parent_identifier = location_device_identifier(
        location.provider, location.provider_id
    )
    parent_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={parent_identifier},
        name=f"{location.name} ({location.provider}:{location.provider_id})",
        manufacturer=location.operator,
        model=location.provider_name,
        serial_number=location.provider_id,
    )
    return parent_device, parent_identifier


def setup_dynamic_station_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: CurbChargeCoordinator,
    async_add_entities: AddEntitiesCallback,
    create_entities: Callable[[ChargingStation], Sequence[Entity]],
) -> None:
    """Add new station entities and remove stations absent for three good polls."""
    platform = async_get_current_platform()
    if not isinstance(platform, EntityPlatform):
        raise RuntimeError("CurbCharge station entities require an active platform")

    entities_by_station: dict[str, list[Entity]] = {}
    parent_identifiers_by_station: dict[str, tuple[str, str]] = {}
    known_station_ids: set[str] = set()
    pending_removals: set[str] = set()

    def add_stations(stations: dict[str, ChargingStation]) -> None:
        device_registry = dr.async_get(hass)
        new_entities: list[Entity] = []
        for station_id, station in stations.items():
            if station_id in known_station_ids:
                continue

            parent_device_info = get_or_create_location_device(
                device_registry, entry, station
            )
            if parent_device_info is not None:
                parent_identifiers_by_station[station_id] = parent_device_info[1]

            entities = list(create_entities(station))
            if parent_device_info is not None:
                parent_device, parent_identifier = parent_device_info
                for entity in entities:
                    if isinstance(entity, CurbChargeStationEntity):
                        entity.attach_to_location_device(
                            station, parent_device, parent_identifier
                        )
            entities_by_station[station_id] = entities
            known_station_ids.add(station_id)
            new_entities.extend(entities)
        if new_entities:
            async_add_entities(new_entities)

    async def remove_stations(station_ids: set[str]) -> None:
        async with coordinator.station_removal_lock:
            entity_registry = er.async_get(hass)
            device_registry = dr.async_get(hass)
            for station_id in station_ids:
                if station_id in (coordinator.data or {}):
                    pending_removals.discard(station_id)
                    continue
                parent_identifier = parent_identifiers_by_station.pop(station_id, None)
                for entity in entities_by_station.pop(station_id, []):
                    if entity.entity_id in platform.entities:
                        await platform.async_remove_entity(entity.entity_id)

                identifier = station_device_identifier(
                    coordinator.provider_id, station_id
                )
                get_child_device = getattr(
                    device_registry, "async_get_child_device_by_identifier", None
                )
                device = (
                    get_child_device(identifier, entry.entry_id)
                    if get_child_device is not None
                    else None
                )
                if device is None:
                    device = next(
                        (
                            candidate
                            for candidate in dr.async_entries_for_config_entry(
                                device_registry, entry.entry_id
                            )
                            if identifier in candidate.identifiers
                        ),
                        None,
                    )
                if device is not None:
                    for registry_entry in er.async_entries_for_device(
                        entity_registry, device.id
                    ):
                        if registry_entry.domain == platform.domain:
                            entity_registry.async_remove(registry_entry.entity_id)
                    if not er.async_entries_for_device(entity_registry, device.id):
                        device_registry.async_remove_device(device.id)

                known_station_ids.discard(station_id)
                pending_removals.discard(station_id)
                if (
                    parent_identifier is None
                    or parent_identifier in parent_identifiers_by_station.values()
                ):
                    continue

                device = next(
                    (
                        candidate
                        for candidate in dr.async_entries_for_config_entry(
                            device_registry, entry.entry_id
                        )
                        if parent_identifier in candidate.identifiers
                    ),
                    None,
                )
                if device is None:
                    continue

                get_children = getattr(dr, "async_entries_for_parent_device", None)
                has_child_devices = (
                    bool(get_children(device_registry, device.id))
                    if get_children is not None
                    else False
                )
                has_legacy_children = any(
                    getattr(candidate, "via_device_id", None) == device.id
                    for candidate in dr.async_entries_for_config_entry(
                        device_registry, entry.entry_id
                    )
                )
                if (
                    not has_child_devices
                    and not has_legacy_children
                    and not er.async_entries_for_device(entity_registry, device.id)
                ):
                    device_registry.async_remove_device(device.id)

    add_stations(coordinator.data or {})

    @callback
    def handle_coordinator_update() -> None:
        add_stations(coordinator.data or {})
        expired = coordinator.removed_station_ids & known_station_ids
        new_removals = expired - pending_removals
        if new_removals:
            pending_removals.update(new_removals)
            hass.async_create_task(remove_stations(new_removals))

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))
