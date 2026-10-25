"""Shared station updates and recommendation logic."""

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Any, NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
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
    DiscoveryFilterSettings,
    FavouriteStationReference,
)
from .provider import (
    AuthenticationError,
    BaseChargingProvider,
    ChargingProviderError,
    ProviderRateLimitError,
)
from .statistics import StatisticsSnapshot, calculate_statistics_snapshot

_LOGGER = logging.getLogger(__name__)


class RecommendationResult(NamedTuple):
    """One selected station and its position in the Best nearby ranking."""

    station: ChargingStation | None
    reason: str | None
    best_nearby_rank: int | None
    best_nearby_rank_score: float | None
    best_nearby_candidate_count: int | None


class RecommendationResults(NamedTuple):
    """All recommendation results computed from one coordinator update."""

    best_nearby: RecommendationResult
    nearest_available: RecommendationResult
    cheapest_available: RecommendationResult
    fastest_available: RecommendationResult
    favorite_nearest: RecommendationResult
    favorite_cheapest: RecommendationResult
    favorite_fastest: RecommendationResult
    best_nearby_ranks: Mapping[tuple[str, str, str], tuple[int, float]]


@dataclass(frozen=True, slots=True)
class _RecommendationCandidate:
    """Facts shared by the recommendation evaluators for one station."""

    station: ChargingStation
    has_available_connectors: bool | None
    available_connectors: int | None
    distance_m: int | None
    max_power_kw: float | None
    best_price_per_kwh: Decimal | None
    best_currency: str | None
    known_price: tuple[str, Decimal] | None


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
        discovery_filters: DiscoveryFilterSettings | None = None,
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
        self.discovery_filters = discovery_filters or DiscoveryFilterSettings()
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
        self.recommendation_results: RecommendationResults | None = None
        self.statistics_snapshot: StatisticsSnapshot | None = None
        self._update_unavailable = False
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=self._default_update_interval,
        )

    @callback
    def async_set_updated_data(self, data: dict[str, ChargingStation]) -> None:
        """Refresh cached summaries before notifying listeners."""
        self.recommendation_results = None
        self._set_recommendation_results(data)
        self._set_statistics_snapshot(data, datetime.now(UTC))
        super().async_set_updated_data(data)

    async def _async_update_data(self) -> dict[str, ChargingStation]:
        """Fetch stations and retain ones temporarily missing upstream."""
        self.recommendation_results = None
        self._update_search_location()
        try:
            stations = await self.provider.async_get_stations(
                self.latitude,
                self.longitude,
                self.radius_m,
                self.max_stations,
                self.favourites,
                discovery_filters=self.discovery_filters,
                include_unknown=self.include_unknown,
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
            update_failed = UpdateFailed(
                "Provider rate limit reached",
                translation_domain=DOMAIN,
                translation_key="rate_limited",
            )
            # Older Home Assistant releases do not define retry_after on UpdateFailed.
            update_failed.retry_after = retry_seconds
            raise update_failed from err
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

        missing_refreshes: dict[str, int] = {}
        removed: set[str] = set()
        for favourite in self.favourites:
            station_id = favourite.provider_id
            if station_id in current:
                continue
            missing_refreshes[station_id] = (
                self._missing_refreshes.get(station_id, 0) + 1
            )
            previous = (self.data or {}).get(station_id)
            current[station_id] = (
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
        self._set_recommendation_results(current)
        self.last_successful_update = datetime.now(UTC)
        self._set_statistics_snapshot(current, self.last_successful_update)
        return current

    def missing_refresh_count(self, station_id: str) -> int:
        """Return successful refreshes since a station last appeared."""
        return self._missing_refreshes.get(station_id, 0)

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
        discovery_filters: DiscoveryFilterSettings | None = None,
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
        if discovery_filters is not None:
            self.discovery_filters = discovery_filters
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

    def _set_recommendation_results(
        self, stations: Mapping[str, ChargingStation]
    ) -> None:
        """Evaluate all recommendation views once for the supplied station data."""
        best_nearby_stations = tuple(
            station
            for station in stations.values()
            if station.distance_m is not None and station.distance_m <= self.radius_m
        )
        if self.nearby_station_ids is None:
            selection_stations = best_nearby_stations
        else:
            selection_stations = tuple(
                station
                for station in stations.values()
                if not station.is_stale
                and station.provider_id in self.nearby_station_ids
            )
        favorite_stations = tuple(
            station
            for favourite in self.favourites
            if (station := stations.get(favourite.provider_id)) is not None
            and not station.is_stale
        )
        self.recommendation_results = evaluate_recommendations(
            best_nearby_stations, selection_stations, favorite_stations
        )

    def _set_statistics_snapshot(
        self,
        stations: Mapping[str, ChargingStation],
        updated_at: datetime,
    ) -> None:
        """Replace the latest aggregate snapshot after a successful data update."""
        self.statistics_snapshot = calculate_statistics_snapshot(
            stations.values(),
            radius_m=self.radius_m,
            updated_at=updated_at,
            max_stations=self.max_stations,
            nearby_station_ids=self.nearby_station_ids,
            favourite_ids=self.favourite_ids,
            include_unknown=self.include_unknown,
        )

    def best_nearby_rank_for(
        self, station: ChargingStation
    ) -> tuple[int | None, float | None, int | None]:
        """Return a station's current Best nearby rank, score, and cohort size."""
        results = self.recommendation_results
        if not self.last_update_success or results is None:
            return None, None, None
        ranking = results.best_nearby_ranks.get(_station_identity_key(station))
        return (
            ranking[0] if ranking is not None else None,
            ranking[1] if ranking is not None else None,
            results.best_nearby.best_nearby_candidate_count,
        )

    @property
    def best_station(self) -> ChargingStation | None:
        """Return the recommended currently reported station."""
        if not self.last_update_success or self.recommendation_results is None:
            return None
        return self.recommendation_results.best_nearby.station

    @property
    def best_station_reason(self) -> str | None:
        """Return a stable reason code for the recommendation."""
        if not self.last_update_success or self.recommendation_results is None:
            return None
        return self.recommendation_results.best_nearby.reason


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
            name=favourite.location_id,
            latitude=favourite.latitude,
            longitude=favourite.longitude,
            address=None,
            operator=None,
            last_updated=None,
        )
        if favourite.location_id is not None
        else None
    )
    return ChargingStation(
        provider_id=favourite.provider_id,
        name=favourite.provider_id,
        latitude=favourite.latitude,
        longitude=favourite.longitude,
        address=None,
        operator=None,
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


def evaluate_recommendations(
    best_nearby_stations: Iterable[ChargingStation],
    selection_stations: Iterable[ChargingStation],
    favorite_stations: Iterable[ChargingStation] = (),
) -> RecommendationResults:
    """Evaluate all station recommendation views from shared candidate facts."""
    best_stations = tuple(best_nearby_stations)
    selected_stations = tuple(selection_stations)
    favorites = tuple(favorite_stations)
    candidates_by_object: dict[int, _RecommendationCandidate] = {}
    for station in (*best_stations, *selected_stations, *favorites):
        station_object_id = id(station)
        if station_object_id not in candidates_by_object:
            tariff = station.tariff
            best_price = tariff.price_per_kwh if tariff is not None else None
            candidates_by_object[station_object_id] = _RecommendationCandidate(
                station=station,
                has_available_connectors=station.has_available_connectors,
                available_connectors=station.available_connectors,
                distance_m=station.distance_m,
                max_power_kw=station.max_power_kw,
                best_price_per_kwh=best_price,
                best_currency=(
                    tariff.currency
                    if tariff is not None and best_price is not None
                    else None
                ),
                known_price=station.comparable_price_per_kwh,
            )

    best_candidates: list[_RecommendationCandidate] = []
    for station in best_stations:
        candidate = candidates_by_object[id(station)]
        if not station.is_stale and candidate.has_available_connectors is True:
            best_candidates.append(candidate)
    ranked_best, best_reason = _rank_best_nearby(best_candidates)
    candidate_count = len(ranked_best)
    best_nearby_ranks = {
        _station_identity_key(candidate.station): (
            rank,
            100.0 * (candidate_count - rank + 1) / candidate_count,
        )
        for rank, candidate in enumerate(ranked_best, 1)
    }

    def result(
        station: ChargingStation | None, reason: str | None
    ) -> RecommendationResult:
        ranking = (
            best_nearby_ranks.get(_station_identity_key(station))
            if station is not None
            else None
        )
        return RecommendationResult(
            station,
            reason,
            ranking[0] if ranking is not None else None,
            ranking[1] if ranking is not None else None,
            candidate_count,
        )

    selector_candidates = _available_station_candidates(
        candidates_by_object[id(station)] for station in selected_stations
    )
    favorite_candidates = _available_station_candidates(
        candidates_by_object[id(station)] for station in favorites
    )
    nearest_station, nearest_reason = _select_nearest_candidate(selector_candidates)
    cheapest_station, cheapest_reason = _select_cheapest_candidate(selector_candidates)
    fastest_station, fastest_reason = _select_fastest_candidate(selector_candidates)
    favorite_nearest, favorite_nearest_reason = _select_nearest_candidate(
        favorite_candidates
    )
    favorite_cheapest, favorite_cheapest_reason = _select_cheapest_candidate(
        favorite_candidates
    )
    favorite_fastest, favorite_fastest_reason = _select_fastest_candidate(
        favorite_candidates
    )
    return RecommendationResults(
        result(ranked_best[0].station if ranked_best else None, best_reason),
        result(nearest_station, nearest_reason),
        result(cheapest_station, cheapest_reason),
        result(fastest_station, fastest_reason),
        result(favorite_nearest, favorite_nearest_reason),
        result(favorite_cheapest, favorite_cheapest_reason),
        result(favorite_fastest, favorite_fastest_reason),
        best_nearby_ranks,
    )


def recommend_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str | None]:
    """Rank available stations without comparing unlike currencies."""
    result = evaluate_recommendations(stations, ()).best_nearby
    return result.station, result.reason


def select_nearest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the closest fresh station with known positive availability."""
    result = evaluate_recommendations((), stations).nearest_available
    return result.station, result.reason or "no_available_station"


def select_cheapest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the cheapest fresh available station within one known currency."""
    result = evaluate_recommendations((), stations).cheapest_available
    return result.station, result.reason or "no_available_station"


def select_fastest_station(
    stations: Iterable[ChargingStation],
) -> tuple[ChargingStation | None, str]:
    """Select the fresh available station with the highest positive power."""
    result = evaluate_recommendations((), stations).fastest_available
    return result.station, result.reason or "no_available_station"


def _rank_best_nearby(
    candidates: list[_RecommendationCandidate],
) -> tuple[list[_RecommendationCandidate], str | None]:
    """Preserve the established Best nearby order and reason codes."""
    if not candidates:
        return [], None
    if len(candidates) == 1:
        return candidates, "only_available_station"

    currencies = {
        candidate.best_currency
        for candidate in candidates
        if candidate.best_price_per_kwh is not None
    }
    compare_prices = len(currencies) <= 1
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            candidate.best_price_per_kwh is None or not compare_prices,
            (
                candidate.best_price_per_kwh
                if compare_prices and candidate.best_price_per_kwh is not None
                else Decimal(0)
            ),
            *_distance_key(candidate.distance_m),
            -(candidate.available_connectors or 0),
            -(candidate.max_power_kw or 0),
            *_station_identity_key(candidate.station),
        ),
    )
    winner = ranked[0]
    winner_price = winner.best_price_per_kwh if compare_prices else None
    known_prices = [
        candidate.best_price_per_kwh
        for candidate in candidates
        if candidate.best_price_per_kwh is not None
    ]
    if winner_price is not None:
        if any(price != winner_price for price in known_prices):
            return ranked, "lowest_price_per_kwh"
        if any(candidate.best_price_per_kwh is None for candidate in candidates):
            return ranked, "known_price_per_kwh"

    if len({candidate.distance_m for candidate in candidates}) > 1:
        if not compare_prices:
            return ranked, "shortest_distance_mixed_currencies"
        if not known_prices:
            return ranked, "shortest_distance_price_unknown"
        return ranked, "shortest_distance_equal_prices"

    if len({candidate.available_connectors for candidate in candidates}) > 1:
        return ranked, "more_available_connectors"
    if len({candidate.max_power_kw for candidate in candidates}) > 1:
        return ranked, "higher_maximum_power"
    if not compare_prices:
        return ranked, "stable_station_id_mixed_currencies"
    if not known_prices:
        return ranked, "stable_station_id_price_unknown"
    return ranked, "stable_station_id"


def _select_nearest_candidate(
    candidates: Iterable[_RecommendationCandidate],
) -> tuple[ChargingStation | None, str]:
    candidates = list(candidates)
    if not candidates:
        return None, "no_available_station"
    candidates_with_distance = [
        candidate for candidate in candidates if candidate.distance_m is not None
    ]
    if not candidates_with_distance:
        return None, "no_known_distance"
    winner = min(
        candidates_with_distance,
        key=lambda candidate: (
            *_distance_key(candidate.distance_m),
            *_station_identity_key(candidate.station),
        ),
    )
    return winner.station, "shortest_distance"


def _select_cheapest_candidate(
    candidates: Iterable[_RecommendationCandidate],
) -> tuple[ChargingStation | None, str]:
    candidates = list(candidates)
    if not candidates:
        return None, "no_available_station"
    priced_candidates = [
        (candidate, candidate.known_price)
        for candidate in candidates
        if candidate.known_price is not None
    ]
    if not priced_candidates:
        return None, "no_known_price"
    currencies = {price[0] for _, price in priced_candidates}
    if len(currencies) > 1:
        return None, "mixed_currencies"
    winner, _price = min(
        priced_candidates,
        key=lambda candidate: (
            candidate[1][1],
            *_distance_key(candidate[0].distance_m),
            -(candidate[0].available_connectors or 0),
            *_descending_power_key(candidate[0].max_power_kw),
            *_station_identity_key(candidate[0].station),
        ),
    )
    return winner.station, "lowest_price_per_kwh"


def _select_fastest_candidate(
    candidates: Iterable[_RecommendationCandidate],
) -> tuple[ChargingStation | None, str]:
    candidates = list(candidates)
    if not candidates:
        return None, "no_available_station"
    powered_candidates: list[tuple[_RecommendationCandidate, float]] = []
    for candidate in candidates:
        power = candidate.max_power_kw
        if power is not None and isfinite(power) and power > 0:
            powered_candidates.append((candidate, power))
    if not powered_candidates:
        return None, "no_positive_power"

    currencies = {
        candidate.known_price[0]
        for candidate, _power in powered_candidates
        if candidate.known_price is not None
    }
    compare_prices = len(currencies) <= 1
    winner, _power = min(
        powered_candidates,
        key=lambda candidate: (
            -candidate[1],
            *_distance_key(candidate[0].distance_m),
            -(candidate[0].available_connectors or 0),
            *_comparable_price_key(candidate[0], compare_prices),
            *_station_identity_key(candidate[0].station),
        ),
    )
    return winner.station, "highest_maximum_power"


def _available_station_candidates(
    candidates: Iterable[_RecommendationCandidate],
) -> list[_RecommendationCandidate]:
    return [
        candidate
        for candidate in candidates
        if not candidate.station.is_stale
        and candidate.available_connectors is not None
        and candidate.available_connectors > 0
    ]


def _distance_key(distance_m: int | None) -> tuple[bool, int]:
    return (
        distance_m is None,
        distance_m if distance_m is not None else 0,
    )


def _descending_power_key(power: float | None) -> tuple[bool, float]:
    known_power = power if power is not None and isfinite(power) else None
    return (
        known_power is None,
        -known_power if known_power is not None else 0.0,
    )


def _comparable_price_key(
    candidate: _RecommendationCandidate, compare_prices: bool
) -> tuple[bool, Decimal]:
    price = (
        candidate.known_price[1]
        if compare_prices and candidate.known_price is not None
        else None
    )
    return price is None, price if price is not None else Decimal(0)


def _station_identity_key(station: ChargingStation) -> tuple[str, str, str]:
    return (
        station.provider.casefold(),
        station.provider_id.casefold(),
        station.provider_id,
    )
