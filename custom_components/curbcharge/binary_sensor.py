"""Binary sensors for CurbCharge stations."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CurbChargeCoordinator
from .entity import CurbChargeStationEntity, setup_dynamic_station_entities
from .models import ChargingStation


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CurbChargeCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up station availability entities."""
    setup_dynamic_station_entities(
        hass,
        entry,
        entry.runtime_data,
        async_add_entities,
        lambda station: create_station_entities(entry.runtime_data, station),
    )


class StationAvailableBinarySensor(CurbChargeStationEntity, BinarySensorEntity):
    """Whether the station reports at least one available connector."""

    _attr_translation_key = "available"

    @property
    def is_on(self) -> bool | None:
        """Return availability only when the provider reports it."""
        station = self.station
        return None if station is None else station.has_available_connectors


def create_station_entities(
    coordinator: CurbChargeCoordinator, station: ChargingStation
) -> list[StationAvailableBinarySensor]:
    """Create the binary sensor for one station."""
    return [StationAvailableBinarySensor(coordinator, station)]
