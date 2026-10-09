"""Shared station updates and recommendation logic."""

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DOMAIN,
    MISSING_STATION_REFRESHES,
    PROVIDER_NDW_DOT_NL,
)
from .models import ChargingStation
from .provider import (
    AuthenticationError,
    BaseChargingProvider,
    ChargingProviderError,
    ProviderRateLimitError,
)

_LOGGER = logging.getLogger(__name__)


class CurbChargeCoordinator(DataUpdateCoordinator[dict[str, ChargingStation]]):
    """Fetch and retain station data once for all entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        provider: BaseChargingProvider,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_stations: int,
        scan_interval_minutes: int,
        include_unknown: bool,
        config_entry: ConfigEntry[Any] | None = None,
    ) -> None:
        """Initialize the shared coordinator."""
        self.provider = provider
        self.provider_id = PROVIDER_NDW_DOT_NL
        self.latitude = latitude
        self.longitude = longitude
        self.radius_m = radius_m
        self.max_stations = max_stations
        self.scan_interval_minutes = scan_interval_minutes
        self.include_unknown = include_unknown
        self._default_update_interval = timedelta(minutes=scan_interval_minutes)
        self._missing_refreshes: dict[str, int] = {}
        self.station_removal_lock = asyncio.Lock()
        self.removed_station_ids: set[str] = set()
        self.last_successful_update: datetime | None = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=self._default_update_interval,
        )

    async def _async_update_data(self) -> dict[str, ChargingStation]:
        """Fetch stations and retain ones temporarily missing upstream."""
        try:
            stations = await self.provider.async_get_stations(
                self.latitude,
                self.longitude,
                self.radius_m,
                self.max_stations,
            )
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                "Authentication with the charging provider failed"
            ) from err
        except ProviderRateLimitError as err:
            retry_seconds = err.retry_after or int(
                self._default_update_interval.total_seconds()
            )
            self.update_interval = max(
                self._default_update_interval, timedelta(seconds=retry_seconds)
            )
            _LOGGER.warning(
                "NDW DOT-NL rate limited an update; retrying after %s seconds",
                int(self.update_interval.total_seconds()),
            )
            raise UpdateFailed("NDW DOT-NL rate limit reached") from err
        except ChargingProviderError as err:
            _LOGGER.warning("CurbCharge update failed (%s)", type(err).__name__)
            raise UpdateFailed("Could not update charging station data") from err

        self.update_interval = self._default_update_interval
        current = {
            station.provider_id: station
            for station in stations
            if self.include_unknown or station.availability_reported
        }
        missing_refreshes: dict[str, int] = {}
        removed: set[str] = set()
        for station_id, previous in (self.data or {}).items():
            if station_id in current:
                continue
            missing_count = self._missing_refreshes.get(station_id, 0) + 1
            if missing_count >= MISSING_STATION_REFRESHES:
                removed.add(station_id)
                continue
            current[station_id] = replace(previous, is_stale=True)
            missing_refreshes[station_id] = missing_count

        self._missing_refreshes = missing_refreshes
        self.removed_station_ids = removed
        self.last_successful_update = datetime.now(UTC)
        return current

    @property
    def best_station(self) -> ChargingStation | None:
        """Return the recommended currently reported station."""
        station, _ = recommend_station((self.data or {}).values())
        return station

    @property
    def best_station_reason(self) -> str | None:
        """Return a stable reason code for the recommendation."""
        _, reason = recommend_station((self.data or {}).values())
        return reason


def recommend_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str | None]:
    """Rank available stations without comparing unlike currencies."""
    candidates = [
        station
        for station in stations
        if not station.is_stale and station.has_available_connectors is True
    ]
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], "only_available_station"

    currencies = {
        station.tariff.currency
        for station in candidates
        if station.tariff is not None and station.tariff.price_per_kwh is not None
    }
    compare_prices = len(currencies) <= 1

    def sort_key(station: ChargingStation) -> tuple[Any, ...]:
        price = (
            station.tariff.price_per_kwh
            if compare_prices and station.tariff is not None
            else None
        )
        return (
            price is None,
            price if price is not None else Decimal(0),
            station.distance_m is None,
            station.distance_m if station.distance_m is not None else 0,
            -(station.available_connectors or 0),
            -(station.max_power_kw or 0),
            station.provider.casefold(),
            station.provider_id.casefold(),
            station.provider_id,
        )

    ranked = sorted(candidates, key=sort_key)
    winner = ranked[0]
    winner_price = (
        winner.tariff.price_per_kwh
        if compare_prices and winner.tariff is not None
        else None
    )
    known_prices = [
        station.tariff.price_per_kwh
        for station in candidates
        if station.tariff is not None and station.tariff.price_per_kwh is not None
    ]
    if winner_price is not None:
        if any(price != winner_price for price in known_prices):
            return winner, "lowest_price_per_kwh"
        if any(
            station.tariff is None or station.tariff.price_per_kwh is None
            for station in candidates
        ):
            return winner, "known_price_per_kwh"

    if len({station.distance_m for station in candidates}) > 1:
        if not compare_prices:
            return winner, "shortest_distance_mixed_currencies"
        if not known_prices:
            return winner, "shortest_distance_price_unknown"
        return winner, "shortest_distance_equal_prices"

    if len({station.available_connectors for station in candidates}) > 1:
        return winner, "more_available_connectors"
    if len({station.max_power_kw for station in candidates}) > 1:
        return winner, "higher_maximum_power"
    if not compare_prices:
        return winner, "stable_station_id_mixed_currencies"
    if not known_prices:
        return winner, "stable_station_id_price_unknown"
    return winner, "stable_station_id"
