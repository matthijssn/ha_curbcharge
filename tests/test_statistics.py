"""Tests for pure current-snapshot station statistics."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from custom_components.curbcharge.const import (
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.models import (
    ChargingConnector,
    ChargingStation,
    ChargingTariff,
    ConnectorStatus,
    ProviderTimestampSource,
    TariffStatus,
)
from custom_components.curbcharge.statistics import (
    MAX_OPERATOR_SUMMARIES,
    availability_statistics,
    calculate_statistics_snapshot,
    tariff_statistics,
)


def _connector(
    connector_id: str,
    count: int,
    available_count: int | None,
    power_kw: float | None = None,
) -> ChargingConnector:
    return ChargingConnector(
        provider_connector_id=connector_id,
        connector_type="IEC_62196_T2",
        status=ConnectorStatus.UNKNOWN,
        max_power_kw=power_kw,
        count=count,
        available_count=available_count,
    )


def _station(
    base: ChargingStation,
    provider_id: str,
    *,
    operator: str | None = "Example CPO",
    provider: str = PROVIDER_NDW_DOT_NL,
    distance_m: int | None = 100,
    price: str | None = "0.25",
    currency: str = "EUR",
    tariff_status: TariffStatus = TariffStatus.AVAILABLE,
    power_kw: float | None = 50,
    updated_at: datetime | None = None,
) -> ChargingStation:
    tariff = (
        ChargingTariff(
            currency,
            Decimal(price),
            None,
            None,
            None,
            "test",
            status=tariff_status,
        )
        if price is not None
        else None
    )
    return replace(
        base,
        provider_id=provider_id,
        provider=provider,
        name=operator or provider_id,
        operator=operator,
        distance_m=distance_m,
        connectors=(_connector(f"{provider_id}:connector", 1, 1, power_kw),),
        tariff=tariff,
        tariff_status=tariff_status if tariff is not None else None,
        last_updated=updated_at,
        last_updated_source=(
            ProviderTimestampSource.NDW_FEATURE if updated_at is not None else None
        ),
        is_stale=False,
    )


def test_availability_statistics_weight_known_groups_and_keep_unknowns():
    base = ChargingStation(
        provider_id="base",
        name="Base",
        latitude=52.0,
        longitude=5.0,
        address=None,
        operator=None,
        distance_m=100,
        connectors=(),
        tariff=None,
        last_updated=None,
    )
    partial = replace(
        base,
        provider_id="partial",
        connectors=(_connector("known", 3, 1), _connector("partial", 2, None)),
    )
    zero = replace(
        base,
        provider_id="zero",
        connectors=(_connector("zero", 2, 0),),
    )
    unknown = replace(
        base,
        provider_id="unknown",
        connectors=(_connector("unknown", 4, None),),
    )
    unreported = replace(
        base,
        provider_id="unreported",
        availability_reported=False,
        connector_count_reported=False,
        connectors=(_connector("unreported", 5, None),),
    )

    summary = availability_statistics((partial, zero, unknown, unreported))

    assert summary.available_connectors == 1
    assert summary.total_connectors == 5
    assert summary.reported_total_connectors == 11
    assert summary.known_connector_group_count == 2
    assert summary.excluded_connector_group_count == 3
    assert summary.unknown_availability_station_count == 3
    assert summary.unreported_total_station_count == 1
    assert summary.known_availability_station_count == 1
    assert summary.available_station_count == 1
    assert summary.contributing_station_count == 2


def test_availability_statistics_distinguish_missing_and_successful_empty():
    assert availability_statistics(None).available_connectors is None
    empty = availability_statistics(())
    assert empty.available_connectors == 0
    assert empty.total_connectors == 0
    assert empty.unknown_availability_station_count == 0


def test_station_comparable_price_is_finite_available_and_currency_normalized(
    sample_stations,
):
    now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    zero_price = _station(sample_stations[0], "zero", price="0.00", currency=" eur ")
    assert zero_price.comparable_price_per_kwh == ("EUR", Decimal("0.00"))

    invalid = (
        replace(zero_price, tariff=None, tariff_status=None),
        replace(
            zero_price,
            tariff=ChargingTariff("EUR", Decimal("NaN"), None, None, None, "test"),
        ),
        replace(zero_price, tariff=replace(zero_price.tariff, currency="  ")),
        replace(zero_price, tariff_status=TariffStatus.AMBIGUOUS),
        replace(
            zero_price,
            tariff=ChargingTariff(
                "EUR",
                Decimal("0.20"),
                None,
                None,
                None,
                "test",
                status=TariffStatus.CONDITIONAL,
            ),
        ),
    )
    assert all(station.comparable_price_per_kwh is None for station in invalid)
    assert zero_price.provider_data_age_seconds(now) is None


def test_tariff_statistics_include_zero_and_weight_stations_not_groups(sample_stations):
    first = _station(sample_stations[0], "zero", price="0.00")
    first = replace(
        first,
        connectors=(_connector("first-a", 2, 1), _connector("first-b", 1, 1)),
    )
    second = _station(sample_stations[1], "positive", price="0.30")
    missing = _station(sample_stations[2], "missing", price=None)
    conditional = _station(
        sample_stations[0],
        "conditional",
        price="0.40",
        tariff_status=TariffStatus.CONDITIONAL,
    )

    summary = tariff_statistics((first, second, missing, conditional))

    assert summary.currency_status == "single_currency"
    assert summary.currency == "EUR"
    assert summary.known_price_station_count == 2
    assert summary.comparable_price_station_count == 2
    assert summary.unknown_price_station_count == 2
    assert summary.invalid_tariff_station_count == 1
    assert summary.minimum_price_per_kwh == Decimal("0.00")
    assert summary.average_price_per_kwh == Decimal("0.15")
    assert summary.maximum_price_per_kwh == Decimal("0.30")
    assert summary.tariff_status_counts == (
        ("available", 2),
        ("conditional", 1),
        ("missing", 1),
    )


def test_tariff_statistics_exclude_mixed_currencies_and_missing_prices(
    sample_stations,
):
    euro = _station(sample_stations[0], "euro", price="0.10", currency="EUR")
    dollar = _station(sample_stations[1], "dollar", price="0.20", currency="USD")
    missing = _station(sample_stations[2], "missing", price=None)

    summary = tariff_statistics((euro, dollar, missing))

    assert summary.currency_status == "mixed_currencies"
    assert summary.currency is None
    assert summary.known_price_station_count == 2
    assert summary.comparable_price_station_count == 0
    assert summary.mixed_currency_station_count == 2
    assert summary.unknown_price_station_count == 1
    assert summary.minimum_price_per_kwh is None
    assert summary.average_price_per_kwh is None
    assert summary.maximum_price_per_kwh is None
    assert tariff_statistics((missing,)).currency_status == "no_known_price"


@pytest.mark.parametrize(
    "status,expected_invalid",
    (
        (TariffStatus.MISSING, 0),
        (TariffStatus.NOT_REPORTED, 0),
        (TariffStatus.CONDITIONAL, 1),
        (TariffStatus.INCOMPLETE, 1),
        (TariffStatus.INACTIVE, 1),
        (TariffStatus.AMBIGUOUS, 1),
    ),
)
def test_tariff_statistics_exclude_non_available_statuses(
    sample_stations, status, expected_invalid
):
    station = _station(
        sample_stations[0],
        status.value,
        price="0.25",
        tariff_status=status,
    )

    summary = tariff_statistics((station,))

    assert summary.currency_status == "no_known_price"
    assert summary.known_price_station_count == 0
    assert summary.unknown_price_station_count == 1
    assert summary.invalid_tariff_station_count == expected_invalid


def test_snapshot_excludes_stale_and_out_of_radius_stations(sample_stations):
    updated_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    fresh = _station(
        sample_stations[0],
        "fresh",
        updated_at=updated_at - timedelta(seconds=75),
    )
    stale = replace(
        _station(sample_stations[1], "stale", operator="Stale Operator"),
        is_stale=True,
    )
    remote = _station(
        sample_stations[2],
        "remote",
        operator="Remote Operator",
        distance_m=600,
    )

    snapshot = calculate_statistics_snapshot(
        (fresh, stale, remote),
        radius_m=500,
        updated_at=updated_at,
        max_stations=2,
        nearby_station_ids={"fresh", "stale", "remote"},
        favourite_ids=set(),
        include_unknown=True,
    )

    assert snapshot.evaluated_station_count == 2
    assert snapshot.fresh_station_count == 1
    assert snapshot.stale_station_count == 1
    assert snapshot.availability.available_connectors == 1
    assert snapshot.tariffs.known_price_station_count == 1
    assert snapshot.provider_timestamp_coverage_count == 1
    assert snapshot.provider_timestamp_missing_count == 0
    assert snapshot.oldest_provider_data_age_seconds == 75
    assert snapshot.newest_provider_data_age_seconds == 75
    assert snapshot.results_may_be_truncated is None
    assert snapshot.data_quality_status == "partial"
    assert snapshot.operator_group_count == 1
    assert snapshot.operator_summaries[0].operator == "Example CPO"
    assert "Stale Operator" not in {
        summary.operator for summary in snapshot.operator_summaries
    }
    with pytest.raises(FrozenInstanceError):
        snapshot.fresh_station_count = 2


def test_snapshot_includes_in_radius_favorites_but_not_remote_favorites(
    sample_stations,
):
    base = sample_stations[0]
    normal = _station(base, "normal", operator="Normal operator")
    nearby_favorite = _station(base, "nearby-favorite", operator="Favorite operator")
    remote_favorite = _station(
        base, "remote-favorite", operator="Remote operator", distance_m=1_000
    )

    snapshot = calculate_statistics_snapshot(
        (normal, nearby_favorite, remote_favorite),
        radius_m=500,
        updated_at=datetime(2025, 1, 2, tzinfo=UTC),
        max_stations=1,
        nearby_station_ids=None,
        favourite_ids={"nearby-favorite", "remote-favorite"},
        include_unknown=True,
    )

    assert snapshot.evaluated_station_count == 2
    assert snapshot.fresh_station_count == 2
    assert snapshot.results_may_be_truncated is True
    assert {summary.operator for summary in snapshot.operator_summaries} == {
        "Normal operator",
        "Favorite operator",
    }


def test_snapshot_quality_status_empty_stale_complete_and_truncated(sample_stations):
    updated_at = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    complete = _station(
        sample_stations[0],
        "complete",
        updated_at=updated_at - timedelta(seconds=10),
    )

    def calculate(stations, *, max_stations=10):
        return calculate_statistics_snapshot(
            stations,
            radius_m=500,
            updated_at=updated_at,
            max_stations=max_stations,
            nearby_station_ids={station.provider_id for station in stations},
            favourite_ids=set(),
            include_unknown=True,
        )

    assert calculate(()).data_quality_status == "empty"
    assert calculate((replace(complete, is_stale=True),)).data_quality_status == "stale"
    complete_snapshot = calculate((complete,))
    assert complete_snapshot.data_quality_status == "complete"
    assert complete_snapshot.results_may_be_truncated is False
    truncated = calculate((complete,), max_stations=1)
    assert truncated.results_may_be_truncated is True
    assert truncated.data_quality_status == "partial"

    missing_timestamp = replace(complete, last_updated=None, last_updated_source=None)
    missing_timestamp_snapshot = calculate((missing_timestamp,))
    assert missing_timestamp_snapshot.provider_timestamp_coverage_count == 0
    assert missing_timestamp_snapshot.provider_timestamp_missing_count == 1
    assert missing_timestamp_snapshot.oldest_provider_data_age_seconds is None
    assert missing_timestamp_snapshot.newest_provider_data_age_seconds is None
    assert missing_timestamp_snapshot.data_quality_status == "partial"


def test_operator_summaries_normalize_names_scope_providers_and_cap_rows(
    sample_stations,
):
    base = sample_stations[0]
    now = datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    first = _station(
        base,
        "nl-one",
        operator=" Example   CPO ",
        price="0.10",
        updated_at=now,
        power_kw=50,
    )
    second = _station(
        base,
        "nl-two",
        operator="example cpo",
        price="0.30",
        currency="USD",
        updated_at=now,
        power_kw=100,
    )
    belgium = _station(
        base,
        "be-one",
        provider=PROVIDER_ECO_MOVEMENT_BE,
        operator="Example CPO",
        updated_at=now,
    )
    more_operators = tuple(
        _station(
            base,
            f"operator-{index}",
            operator=f"Operator {index:02d}",
            updated_at=now,
        )
        for index in range(10)
    )
    unattributed = _station(base, "unattributed", operator=None, updated_at=now)

    snapshot = calculate_statistics_snapshot(
        (first, second, belgium, *more_operators, unattributed),
        radius_m=500,
        updated_at=now,
        max_stations=50,
        nearby_station_ids=None,
        favourite_ids=set(),
        include_unknown=True,
    )

    assert snapshot.operator_group_count == 12
    assert snapshot.operator_list_truncated
    assert snapshot.unattributed_station_count == 1
    assert snapshot.data_quality_status == "partial"
    assert len(snapshot.operator_summaries) == MAX_OPERATOR_SUMMARIES
    nl_operator = snapshot.operator_summaries[0]
    assert nl_operator.provider == PROVIDER_NDW_DOT_NL
    assert nl_operator.normalized_name == "example cpo"
    assert nl_operator.station_count == 2
    assert nl_operator.currency_status == "mixed_currencies"
    assert nl_operator.known_price_station_count == 2
    assert nl_operator.comparable_price_station_count == 0
    assert nl_operator.minimum_price_per_kwh is None
    assert nl_operator.average_price_per_kwh is None
    assert nl_operator.average_max_power_kw == 75
    assert snapshot.operator_summaries[1].provider == PROVIDER_ECO_MOVEMENT_BE
    assert all(
        not hasattr(summary, "station_id") for summary in snapshot.operator_summaries
    )


def test_operator_name_normalization_is_exact_and_does_not_merge_punctuation(
    sample_stations,
):
    base = sample_stations[0]
    stations = (
        _station(base, "punctuation-one", operator="Acme, Inc", power_kw=None),
        _station(base, "punctuation-two", operator="Acme Inc", power_kw=None),
    )

    snapshot = calculate_statistics_snapshot(
        stations,
        radius_m=500,
        updated_at=datetime(2025, 1, 2, tzinfo=UTC),
        max_stations=10,
        nearby_station_ids=None,
        favourite_ids=set(),
        include_unknown=True,
    )

    assert snapshot.operator_group_count == 2
    assert {summary.normalized_name for summary in snapshot.operator_summaries} == {
        "acme, inc",
        "acme inc",
    }
    assert all(
        summary.average_max_power_kw is None for summary in snapshot.operator_summaries
    )
