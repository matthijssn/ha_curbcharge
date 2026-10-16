"""Shared station updates and recommendation logic."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import (
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DOMAIN,
    MISSING_STATION_REFRESHES,
)
from .geography import distance_m
from .models import (
    ChargingLocation,
    ChargingStation,
    FavouriteStationReference,
    TariffStatus,
)
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
        favourites: tuple[FavouriteStationReference, ...] = (),
        config_entry: ConfigEntry[Any] | None = None,
        location_resolver: Callable[[], tuple[float, float] | None] | None = None,
        location_movement_threshold_m: int = DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    ) -> None:
        """Initialize the shared coordinator."""
        self.provider = provider
        self.provider_id = provider.provider_id
        self.provider_name = provider.provider_name
        self.country_code = provider.country_code
        self.latitude = latitude
        self.longitude = longitude
        self.radius_m = radius_m
        self.max_stations = max_stations
        self.scan_interval_minutes = scan_interval_minutes
        self.include_unknown = include_unknown
        self.favourites = favourites
        self.location_resolver = location_resolver
        self.location_movement_threshold_m = location_movement_threshold_m
        self.favourite_ids = {favourite.provider_id for favourite in favourites}
        self.nearby_station_ids: set[str] | None = None
        self._config_entry_data = (
            dict(config_entry.data) if config_entry is not None else None
        )
        self._default_update_interval = timedelta(minutes=scan_interval_minutes)
        self._missing_refreshes: dict[str, int] = {}
        self.station_removal_lock = asyncio.Lock()
        self.removed_station_ids: set[str] = set()
        self.last_successful_update: datetime | None = None
        self._update_unavailable = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=self._default_update_interval,
        )

    async def _async_update_data(self) -> dict[str, ChargingStation]:
        """Fetch stations and retain ones temporarily missing upstream."""
        self._update_search_location()
        try:
            stations = await self.provider.async_get_stations(
                self.latitude,
                self.longitude,
                self.radius_m,
                self.max_stations,
                self.favourites,
            )
        except AuthenticationError as err:
            self._log_update_unavailable(type(err).__name__)
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="authentication_failed",
            ) from err
        except ProviderRateLimitError as err:
            self._log_update_unavailable(type(err).__name__)
            retry_seconds = err.retry_after or int(
                self._default_update_interval.total_seconds()
            )
            self.update_interval = max(
                self._default_update_interval, timedelta(seconds=retry_seconds)
            )
            raise UpdateFailed("NDW DOT-NL rate limit reached") from err
        except ChargingProviderError as err:
            self._log_update_unavailable(type(err).__name__)
            raise UpdateFailed("Could not update charging station data") from err

        if self._update_unavailable:
            _LOGGER.info("CurbCharge data updates have recovered")
            self._update_unavailable = False
        self.update_interval = self._default_update_interval
        self.nearby_station_ids = {station.provider_id for station in stations.nearby}
        current = {
            station.provider_id: station
            for station in stations.nearby
            if (
                self.include_unknown
                or station.availability_reported
                or station.provider_id in self.favourite_ids
            )
        }
        for station in stations.favourites:
            if station.provider_id not in self.favourite_ids:
                raise UpdateFailed("Provider returned an unexpected favourite")
            current[station.provider_id] = station

        for favourite in self.favourites:
            if favourite.provider_id in current:
                continue
            previous = (self.data or {}).get(favourite.provider_id)
            current[favourite.provider_id] = (
                replace(previous, is_stale=True)
                if previous is not None
                else _favourite_placeholder(
                    favourite,
                    self.provider_id,
                    self.provider_name,
                    self.country_code,
                    self.latitude,
                    self.longitude,
                )
            )

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

    def _update_search_location(self) -> None:
        """Resolve and accept a tracked location before each provider refresh."""
        if self.location_resolver is None:
            return

        location = self.location_resolver()
        if location is None:
            self._log_update_unavailable("LocationUnavailable")
            raise UpdateFailed("Configured charging search location is unavailable")

        if (
            self.location_movement_threshold_m == 0
            or distance_m(self.latitude, self.longitude, *location)
            >= self.location_movement_threshold_m
        ):
            self.latitude, self.longitude = location

    def update_options(
        self,
        *,
        radius_m: int,
        max_stations: int,
        scan_interval_minutes: int,
        include_unknown: bool,
        favourites: tuple[FavouriteStationReference, ...],
        location_movement_threshold_m: int = DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    ) -> None:
        """Apply options without discarding station-missing history."""
        previous_default_interval = self._default_update_interval
        current_interval = self.update_interval
        self.radius_m = radius_m
        self.max_stations = max_stations
        self.scan_interval_minutes = scan_interval_minutes
        self.include_unknown = include_unknown
        self.favourites = favourites
        self.location_movement_threshold_m = location_movement_threshold_m
        self.favourite_ids = {favourite.provider_id for favourite in favourites}
        self._default_update_interval = timedelta(minutes=scan_interval_minutes)
        self.update_interval = (
            self._default_update_interval
            if current_interval is None or current_interval == previous_default_interval
            else max(current_interval, self._default_update_interval)
        )

    def entry_data_changed(self, data: Mapping[str, Any]) -> bool:
        """Return whether setup-only config data differs from its snapshot."""
        return self._config_entry_data is not None and self._config_entry_data != dict(
            data
        )

    def _log_update_unavailable(self, error_type: str) -> None:
        """Log only the transition to an unavailable provider."""
        if self._update_unavailable:
            return
        _LOGGER.warning("CurbCharge data update failed (%s)", error_type)
        self._update_unavailable = True

    @property
    def best_station(self) -> ChargingStation | None:
        """Return the recommended currently reported station."""
        station, _ = recommend_station(
            station
            for station in (self.data or {}).values()
            if station.distance_m is not None and station.distance_m <= self.radius_m
        )
        return station

    @property
    def best_station_reason(self) -> str | None:
        """Return a stable reason code for the recommendation."""
        _, reason = recommend_station(
            station
            for station in (self.data or {}).values()
            if station.distance_m is not None and station.distance_m <= self.radius_m
        )
        return reason


def _favourite_placeholder(
    favourite: FavouriteStationReference,
    provider: str,
    provider_name: str,
    country_code: str,
    latitude: float,
    longitude: float,
) -> ChargingStation:
    """Create an unavailable station from saved identity/location only."""
    location = (
        ChargingLocation(
            provider_id=favourite.location_id,
            provider=provider,
            provider_name=provider_name,
            country_code=country_code,
            name=favourite.location_name or favourite.name,
            latitude=favourite.latitude,
            longitude=favourite.longitude,
            address=favourite.location_name,
            operator=favourite.location_operator,
            last_updated=None,
        )
        if favourite.location_id is not None
        else None
    )
    return ChargingStation(
        provider_id=favourite.provider_id,
        name=favourite.name,
        latitude=favourite.latitude,
        longitude=favourite.longitude,
        address=favourite.name,
        operator=favourite.location_operator,
        distance_m=distance_m(
            latitude, longitude, favourite.latitude, favourite.longitude
        ),
        connectors=(),
        tariff=None,
        last_updated=None,
        provider=provider,
        availability_reported=False,
        is_stale=True,
        country_code=country_code,
        location=location,
        provider_name=provider_name,
        connector_count_reported=False,
    )


def recommend_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str | None]:
    """Rank available stations without comparing unlike currencies."""
    candidates = [
        station
        for station in _fresh_stations(stations)
        if station.has_available_connectors is True
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
            *_distance_key(station),
            -(station.available_connectors or 0),
            -(station.max_power_kw or 0),
            *_station_identity_key(station),
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


def select_nearest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the closest fresh station with known positive availability."""
    candidates = _available_station_candidates(stations)
    if not candidates:
        return None, "no_available_station"
    candidates_with_distance = [
        station for station in candidates if station.distance_m is not None
    ]
    if not candidates_with_distance:
        return None, "no_known_distance"

    winner = min(
        candidates_with_distance,
        key=lambda station: (*_distance_key(station), *_station_identity_key(station)),
    )
    return winner, "shortest_distance"


def select_cheapest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the cheapest fresh available station within one known currency."""
    candidates = _available_station_candidates(stations)
    if not candidates:
        return None, "no_available_station"

    priced_candidates: list[tuple[ChargingStation, str, Decimal]] = []
    for station in candidates:
        known_price = _known_station_price(station)
        if known_price is None:
            continue
        currency, price = known_price
        priced_candidates.append((station, currency, price))
    if not priced_candidates:
        return None, "no_known_price"

    currencies = {currency for _, currency, _ in priced_candidates}
    if len(currencies) > 1:
        return None, "mixed_currencies"

    winner = min(
        priced_candidates,
        key=lambda candidate: (
            candidate[2],
            *_distance_key(candidate[0]),
            *_station_identity_key(candidate[0]),
        ),
    )[0]
    return winner, "lowest_price_per_kwh"


def select_fastest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the fresh available station with the highest positive power."""
    candidates = _available_station_candidates(stations)
    if not candidates:
        return None, "no_available_station"
    powered_candidates: list[tuple[ChargingStation, float]] = []
    for station in candidates:
        power = station.max_power_kw
        if power is not None and isfinite(power) and power > 0:
            powered_candidates.append((station, power))
    if not powered_candidates:
        return None, "no_positive_power"

    winner = min(
        powered_candidates,
        key=lambda station: (
            -station[1],
            *_distance_key(station[0]),
            *_station_identity_key(station[0]),
        ),
    )
    return winner[0], "highest_maximum_power"


def _fresh_stations(
    stations: Iterable[ChargingStation],
) -> list[ChargingStation]:
    return [station for station in stations if not station.is_stale]


def _available_station_candidates(
    stations: Iterable[ChargingStation],
) -> list[ChargingStation]:
    return [
        station
        for station in _fresh_stations(stations)
        if (
            station.available_connectors is not None
            and station.available_connectors > 0
        )
    ]


def _known_station_price(station: ChargingStation) -> tuple[str, Decimal] | None:
    tariff = station.tariff
    if (
        tariff is None
        or tariff.price_per_kwh is None
        or not tariff.price_per_kwh.is_finite()
        or not tariff.currency.strip()
        or tariff.effective_status is not TariffStatus.AVAILABLE
        or (
            station.tariff_status is not None
            and station.tariff_status is not TariffStatus.AVAILABLE
        )
    ):
        return None
    return tariff.currency.strip().upper(), tariff.price_per_kwh


def _distance_key(station: ChargingStation) -> tuple[bool, int]:
    return (
        station.distance_m is None,
        station.distance_m if station.distance_m is not None else 0,
    )


def _station_identity_key(station: ChargingStation) -> tuple[str, str, str]:
    return (
        station.provider.casefold(),
        station.provider_id.casefold(),
        station.provider_id,
    )
