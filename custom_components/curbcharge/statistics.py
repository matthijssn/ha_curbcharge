"""Pure current-snapshot availability, tariff, and operator statistics."""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from math import fsum, isfinite

from .models import ChargingStation, TariffStatus

MAX_OPERATOR_SUMMARIES = 10


@dataclass(frozen=True, slots=True)
class AvailabilityStatistics:
    """Weighted connector totals and contributor-quality counts."""

    available_connectors: int | None
    total_connectors: int | None
    reported_total_connectors: int | None
    known_connector_group_count: int | None
    excluded_connector_group_count: int | None
    unknown_availability_station_count: int | None
    unreported_total_station_count: int | None
    known_availability_station_count: int | None
    available_station_count: int | None
    contributing_station_count: int | None


@dataclass(frozen=True, slots=True)
class TariffStatistics:
    """Single-currency station-price summary and explicit exclusions."""

    minimum_price_per_kwh: Decimal | None
    average_price_per_kwh: Decimal | None
    maximum_price_per_kwh: Decimal | None
    currency: str | None
    currency_status: str
    known_price_station_count: int
    comparable_price_station_count: int
    unknown_price_station_count: int
    invalid_tariff_station_count: int
    mixed_currency_station_count: int
    tariff_status_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class OperatorSummary:
    """One provider-scoped, conservatively normalized operator grouping."""

    provider: str
    normalized_name: str
    operator: str
    station_count: int
    known_availability_station_count: int
    available_station_count: int
    unknown_availability_station_count: int
    available_connectors: int | None
    total_connectors: int | None
    reported_total_connectors: int | None
    availability_percentage: float | None
    currency: str | None
    currency_status: str
    known_price_station_count: int
    comparable_price_station_count: int
    minimum_price_per_kwh: Decimal | None
    average_price_per_kwh: Decimal | None
    average_max_power_kw: float | None


@dataclass(frozen=True, slots=True)
class StatisticsSnapshot:
    """Immutable latest-only statistics for one config entry's search area."""

    last_successful_update: datetime
    data_quality_status: str
    evaluated_station_count: int
    fresh_station_count: int
    stale_station_count: int
    availability: AvailabilityStatistics
    tariffs: TariffStatistics
    provider_timestamp_coverage_count: int
    provider_timestamp_missing_count: int
    oldest_provider_data_age_seconds: int | None
    newest_provider_data_age_seconds: int | None
    results_may_be_truncated: bool | None
    unattributed_station_count: int
    operator_group_count: int
    operator_list_truncated: bool
    operator_summaries: tuple[OperatorSummary, ...]


def availability_statistics(
    stations: Iterable[ChargingStation] | None,
) -> AvailabilityStatistics:
    """Summarize explicitly known connector groups without inferring unknowns."""
    if stations is None:
        return AvailabilityStatistics(
            None, None, None, None, None, None, None, None, None, None
        )

    available_connectors = 0
    total_connectors = 0
    reported_total_connectors = 0
    known_connector_group_count = 0
    excluded_connector_group_count = 0
    unknown_availability_station_count = 0
    unreported_total_station_count = 0
    known_availability_station_count = 0
    available_station_count = 0
    contributing_station_count = 0

    for station in stations:
        if station.available_connectors is not None:
            known_availability_station_count += 1
        if station.availability_reported and any(
            connector.known_available_count is not None
            and connector.known_available_count > 0
            for connector in station.connectors
        ):
            available_station_count += 1
        if not station.availability_reported or any(
            connector.known_available_count is None for connector in station.connectors
        ):
            unknown_availability_station_count += 1
        if not station.connector_count_reported:
            unreported_total_station_count += 1
            excluded_connector_group_count += len(station.connectors)
            continue

        reported_total_connectors += sum(
            connector.count for connector in station.connectors
        )
        if not station.availability_reported:
            excluded_connector_group_count += len(station.connectors)
            continue

        station_contributed = False
        for connector in station.connectors:
            available = connector.known_available_count
            if available is None:
                excluded_connector_group_count += 1
                continue
            available_connectors += available
            total_connectors += connector.count
            known_connector_group_count += 1
            station_contributed = True
        if station_contributed:
            contributing_station_count += 1

    return AvailabilityStatistics(
        available_connectors,
        total_connectors,
        reported_total_connectors,
        known_connector_group_count,
        excluded_connector_group_count,
        unknown_availability_station_count,
        unreported_total_station_count,
        known_availability_station_count,
        available_station_count,
        contributing_station_count,
    )


def tariff_statistics(stations: Iterable[ChargingStation]) -> TariffStatistics:
    """Aggregate each station's comparable gross price once, in one currency."""
    station_values = tuple(stations)
    prices: list[tuple[str, Decimal]] = []
    status_counts: Counter[str] = Counter()
    invalid_tariff_station_count = 0

    for station in station_values:
        tariff = station.tariff
        status = station.tariff_status or (
            tariff.effective_status if tariff is not None else TariffStatus.MISSING
        )
        status_counts[status.value] += 1
        price = station.comparable_price_per_kwh
        if price is not None:
            prices.append(price)
        elif status not in (TariffStatus.MISSING, TariffStatus.NOT_REPORTED):
            invalid_tariff_station_count += 1

    currencies = {currency for currency, _price in prices}
    if not prices:
        currency_status = "no_known_price"
        currency = None
    elif len(currencies) > 1:
        currency_status = "mixed_currencies"
        currency = None
    else:
        currency_status = "single_currency"
        currency = next(iter(currencies))

    comparable_prices = (
        [price for _currency, price in prices]
        if currency_status == "single_currency"
        else []
    )
    price_count = len(prices)
    return TariffStatistics(
        minimum_price_per_kwh=min(comparable_prices, default=None),
        average_price_per_kwh=(
            sum(comparable_prices, Decimal(0)) / len(comparable_prices)
            if comparable_prices
            else None
        ),
        maximum_price_per_kwh=max(comparable_prices, default=None),
        currency=currency,
        currency_status=currency_status,
        known_price_station_count=price_count,
        comparable_price_station_count=len(comparable_prices),
        unknown_price_station_count=len(station_values) - price_count,
        invalid_tariff_station_count=invalid_tariff_station_count,
        mixed_currency_station_count=(
            price_count if currency_status == "mixed_currencies" else 0
        ),
        tariff_status_counts=tuple(sorted(status_counts.items())),
    )


def calculate_statistics_snapshot(
    stations: Iterable[ChargingStation],
    *,
    radius_m: int,
    updated_at: datetime,
    max_stations: int,
    nearby_station_ids: Collection[str] | None,
    favourite_ids: Collection[str],
    include_unknown: bool,
) -> StatisticsSnapshot:
    """Calculate the latest bounded snapshot from one coordinator refresh."""
    in_radius = tuple(
        station
        for station in stations
        if station.distance_m is not None and station.distance_m <= radius_m
    )
    current = tuple(station for station in in_radius if not station.is_stale)
    stale_station_count = len(in_radius) - len(current)
    availability = availability_statistics(current)
    tariffs = tariff_statistics(current)
    operator_summaries, unattributed_count, operator_group_count = _operator_summaries(
        current
    )
    operator_list_truncated = operator_group_count > MAX_OPERATOR_SUMMARIES

    ages = [
        age
        for station in current
        if (age := station.provider_data_age_seconds(updated_at)) is not None
    ]
    provider_timestamp_missing_count = len(current) - len(ages)

    normal_station_ids = (
        nearby_station_ids
        if nearby_station_ids is not None
        else {
            station.provider_id
            for station in current
            if station.provider_id not in favourite_ids
        }
    )
    normal_station_count = sum(
        station.provider_id in normal_station_ids for station in current
    )
    if normal_station_count >= max_stations:
        results_may_be_truncated = True
    elif stale_station_count == 0 and include_unknown:
        results_may_be_truncated = False
    else:
        results_may_be_truncated = None

    if not current:
        data_quality_status = "stale" if stale_station_count else "empty"
    elif (
        stale_station_count
        or (availability.unknown_availability_station_count or 0)
        or (availability.unreported_total_station_count or 0)
        or tariffs.unknown_price_station_count
        or tariffs.currency_status == "mixed_currencies"
        or provider_timestamp_missing_count
        or results_may_be_truncated is not False
        or operator_list_truncated
        or unattributed_count
    ):
        data_quality_status = "partial"
    else:
        data_quality_status = "complete"

    return StatisticsSnapshot(
        last_successful_update=updated_at,
        data_quality_status=data_quality_status,
        evaluated_station_count=len(in_radius),
        fresh_station_count=len(current),
        stale_station_count=stale_station_count,
        availability=availability,
        tariffs=tariffs,
        provider_timestamp_coverage_count=len(ages),
        provider_timestamp_missing_count=provider_timestamp_missing_count,
        oldest_provider_data_age_seconds=max(ages, default=None),
        newest_provider_data_age_seconds=min(ages, default=None),
        results_may_be_truncated=results_may_be_truncated,
        unattributed_station_count=unattributed_count,
        operator_group_count=operator_group_count,
        operator_list_truncated=operator_list_truncated,
        operator_summaries=operator_summaries,
    )


def _operator_summaries(
    stations: tuple[ChargingStation, ...],
) -> tuple[tuple[OperatorSummary, ...], int, int]:
    groups: dict[tuple[str, str], list[ChargingStation]] = {}
    display_names: dict[tuple[str, str], set[str]] = {}
    unattributed_count = 0
    for station in stations:
        display_name = station.operator.strip() if station.operator else ""
        normalized_name = " ".join(display_name.split()).casefold()
        if not normalized_name:
            unattributed_count += 1
            continue
        key = (station.provider, normalized_name)
        groups.setdefault(key, []).append(station)
        display_names.setdefault(key, set()).add(display_name)

    summaries: list[OperatorSummary] = []
    for (provider, normalized_name), group in groups.items():
        availability = availability_statistics(group)
        tariffs = tariff_statistics(group)
        powers = [
            station.max_power_kw
            for station in group
            if station.max_power_kw is not None and isfinite(station.max_power_kw)
        ]
        has_known_connector_group = bool(availability.known_connector_group_count)
        total_connectors = (
            availability.total_connectors if has_known_connector_group else None
        )
        summaries.append(
            OperatorSummary(
                provider=provider,
                normalized_name=normalized_name,
                operator=min(
                    display_names[(provider, normalized_name)],
                    key=lambda name: (name.casefold(), name),
                ),
                station_count=len(group),
                known_availability_station_count=(
                    availability.known_availability_station_count or 0
                ),
                available_station_count=availability.available_station_count or 0,
                unknown_availability_station_count=(
                    availability.unknown_availability_station_count or 0
                ),
                available_connectors=(
                    availability.available_connectors
                    if has_known_connector_group
                    else None
                ),
                total_connectors=total_connectors,
                reported_total_connectors=(
                    availability.reported_total_connectors
                    if any(station.connector_count_reported for station in group)
                    else None
                ),
                availability_percentage=(
                    round(
                        100 * availability.available_connectors / total_connectors,
                        1,
                    )
                    if has_known_connector_group
                    and total_connectors is not None
                    and total_connectors > 0
                    and availability.available_connectors is not None
                    else None
                ),
                currency=tariffs.currency,
                currency_status=tariffs.currency_status,
                known_price_station_count=tariffs.known_price_station_count,
                comparable_price_station_count=tariffs.comparable_price_station_count,
                minimum_price_per_kwh=tariffs.minimum_price_per_kwh,
                average_price_per_kwh=tariffs.average_price_per_kwh,
                average_max_power_kw=(fsum(powers) / len(powers) if powers else None),
            )
        )

    summaries.sort(
        key=lambda summary: (
            -summary.station_count,
            summary.provider,
            summary.normalized_name,
        )
    )
    return (
        tuple(summaries[:MAX_OPERATOR_SUMMARIES]),
        unattributed_count,
        len(summaries),
    )
