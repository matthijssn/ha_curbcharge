"""Device trackers for CurbCharge stations."""

from typing import TYPE_CHECKING

from homeassistant.components.device_tracker.const import SourceType

if TYPE_CHECKING:
    from homeassistant.components.device_tracker.entity import TrackerEntity
else:
    # Home Assistant 2024.12 kept TrackerEntity in config_entry.
    try:
        from homeassistant.components.device_tracker.entity import TrackerEntity
    except ImportError:
        from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import CurbChargeCoordinator
from .entity import CurbChargeStationEntity, setup_dynamic_station_entities
from .models import ChargingStation

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[CurbChargeCoordinator],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up station location trackers."""
    coordinator = entry.runtime_data
    setup_dynamic_station_entities(
        hass,
        entry,
        coordinator,
        async_add_entities,
        lambda station: [StationDeviceTracker(coordinator, station)],
    )


class StationDeviceTracker(CurbChargeStationEntity, TrackerEntity):
    """Map location for one charging station."""

    _attr_has_entity_name = False
    _attr_source_type = SourceType.GPS

    def __init__(
        self, coordinator: CurbChargeCoordinator, station: ChargingStation
    ) -> None:
        """Initialize the station tracker."""
        super().__init__(coordinator, station, "location")
        self._attr_name = station.name

    @property
    def latitude(self) -> float | None:
        """Return the station's latest latitude."""
        station = self.station
        return station.latitude if station is not None else None

    @property
    def longitude(self) -> float | None:
        """Return the station's latest longitude."""
        station = self.station
        return station.longitude if station is not None else None
