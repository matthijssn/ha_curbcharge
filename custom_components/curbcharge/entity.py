"""Shared entities and dynamic station lifecycle support."""

from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    EntityPlatform,
    async_get_current_platform,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PROVIDER_NAME
from .coordinator import CurbChargeCoordinator
from .models import ChargingStation


class CurbChargeStationEntity(CoordinatorEntity[CurbChargeCoordinator]):
    """Base class for entities belonging to one charging station."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: CurbChargeCoordinator, station: ChargingStation
    ) -> None:
        """Initialize an entity with stable station identity."""
        super().__init__(coordinator)
        self._station_id = station.provider_id
        self._attr_unique_id = (
            f"{station.provider}_{station.provider_id}_{self._attr_translation_key}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                station_device_identifier(station.provider, station.provider_id)
            },
            name=f"{station.name} ({station.provider}:{station.provider_id})",
            manufacturer=station.operator,
            model=PROVIDER_NAME,
            serial_number=station.provider_id,
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


def setup_dynamic_station_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: CurbChargeCoordinator,
    async_add_entities: AddEntitiesCallback,
    create_entities: Callable[[ChargingStation], list[Entity]],
) -> None:
    """Add new station entities and remove stations absent for three good polls."""
    platform = async_get_current_platform()
    if not isinstance(platform, EntityPlatform):
        raise RuntimeError("CurbCharge station entities require an active platform")

    entities_by_station: dict[str, list[Entity]] = {}
    known_station_ids: set[str] = set()
    pending_removals: set[str] = set()

    def add_stations(stations: dict[str, ChargingStation]) -> None:
        new_entities: list[Entity] = []
        for station_id, station in stations.items():
            if station_id in known_station_ids:
                continue
            entities = create_entities(station)
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
                for entity in entities_by_station.pop(station_id, []):
                    if entity.entity_id is not None:
                        await platform.async_remove_entity(entity.entity_id)

                identifier = station_device_identifier(
                    coordinator.provider_id, station_id
                )
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
                if device is None:
                    known_station_ids.discard(station_id)
                    pending_removals.discard(station_id)
                    continue
                for registry_entry in er.async_entries_for_device(
                    entity_registry, device.id
                ):
                    if registry_entry.domain == platform.domain:
                        entity_registry.async_remove(registry_entry.entity_id)
                if not er.async_entries_for_device(entity_registry, device.id):
                    device_registry.async_remove_device(device.id)
                known_station_ids.discard(station_id)
                pending_removals.discard(station_id)

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
