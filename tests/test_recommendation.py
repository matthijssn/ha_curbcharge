"""Tests for best-nearby charging station ranking."""

from dataclasses import replace
from decimal import Decimal

from custom_components.curbcharge.coordinator import (
    recommend_station,
    select_cheapest_station,
    select_fastest_station,
    select_nearest_station,
)
from custom_components.curbcharge.models import (
    ChargingTariff,
    EVSEStatus,
    TariffStatus,
)


def _available_station(station, count=1, power=22):
    connector = replace(
        station.connectors[0],
        count=count,
        available_count=count,
        max_power_kw=power,
    )
    return replace(station, connectors=(connector,))


def _with_tariff(station, currency, price):
    return replace(
        station,
        tariff=ChargingTariff(
            currency=currency,
            price_per_kwh=Decimal(price) if price is not None else None,
            session_fee=None,
            price_per_minute=None,
            description=None,
            source="NDW DOT-NL",
        ),
    )


def test_lower_known_price_beats_shorter_distance(sample_stations):
    cheap = _with_tariff(_available_station(sample_stations[0]), "EUR", "0.20")
    nearby = _with_tariff(
        replace(_available_station(sample_stations[1]), distance_m=10),
        "EUR",
        "0.30",
    )

    station, reason = recommend_station((nearby, cheap))

    assert station == cheap
    assert reason == "lowest_price_per_kwh"


def test_unknown_prices_use_shorter_distance(sample_stations):
    farther = replace(_available_station(sample_stations[0]), distance_m=100)
    closer = replace(_available_station(sample_stations[1]), distance_m=20)

    station, reason = recommend_station((farther, closer))

    assert station == closer
    assert reason == "shortest_distance_price_unknown"


def test_mixed_currencies_are_not_compared(sample_stations):
    cheap_foreign_currency = _with_tariff(
        replace(_available_station(sample_stations[0]), distance_m=100),
        "EUR",
        "0.10",
    )
    nearby_other_currency = _with_tariff(
        replace(_available_station(sample_stations[1]), distance_m=1),
        "USD",
        "0.50",
    )

    station, reason = recommend_station((cheap_foreign_currency, nearby_other_currency))

    assert station == nearby_other_currency
    assert reason == "shortest_distance_mixed_currencies"


def test_ordering_is_deterministic_when_all_other_values_tie(sample_stations):
    first = replace(
        _available_station(sample_stations[0], count=2, power=50),
        distance_m=50,
    )
    second = replace(
        _available_station(sample_stations[1], count=2, power=50),
        distance_m=50,
    )

    station, reason = recommend_station((second, first))

    assert station is not None
    assert station.provider_id == min(first.provider_id, second.provider_id)
    assert reason == "stable_station_id_price_unknown"


def test_more_available_connectors_breaks_distance_tie(sample_stations):
    first = replace(
        _available_station(sample_stations[0], count=1, power=50),
        distance_m=50,
    )
    second = replace(
        _available_station(sample_stations[1], count=2, power=50),
        distance_m=50,
    )

    station, reason = recommend_station((first, second))

    assert station == second
    assert reason == "more_available_connectors"


def test_no_station_without_available_connectors_is_recommended(sample_stations):
    unavailable = replace(
        sample_stations[0],
        connectors=tuple(
            replace(connector, available_count=0, status=connector.status.UNKNOWN)
            for connector in sample_stations[0].connectors
        ),
    )

    assert recommend_station((unavailable,)) == (None, None)


def test_nearest_selector_uses_distance_and_stable_station_identity(sample_stations):
    first = replace(_available_station(sample_stations[0]), distance_m=25)
    second = replace(_available_station(sample_stations[1]), distance_m=25)
    stale = replace(first, distance_m=1, is_stale=True)

    station, reason = select_nearest_station((second, stale, first))

    assert station is not None
    assert station.provider_id == min(first.provider_id, second.provider_id)
    assert reason == "shortest_distance"


def test_nearest_selector_excludes_unknown_distance_and_non_candidates(
    sample_stations,
):
    no_distance = replace(_available_station(sample_stations[0]), distance_m=None)
    zero_connectors = _available_station(sample_stations[1], count=0)
    stale = replace(_available_station(sample_stations[0]), is_stale=True)

    assert select_nearest_station((no_distance,)) == (None, "no_known_distance")
    assert select_nearest_station((zero_connectors, stale)) == (
        None,
        "no_available_station",
    )


def test_new_selectors_require_reported_positive_connectors_and_preserve_best_nearby(
    sample_stations,
):
    status_only = replace(
        sample_stations[0],
        connectors=(),
        availability_reported=True,
        connector_count_reported=False,
        evse_status=EVSEStatus.AVAILABLE,
        distance_m=1,
    )

    assert status_only.available_connectors is None
    assert status_only.has_available_connectors is True
    assert recommend_station((status_only,)) == (
        status_only,
        "only_available_station",
    )
    zero_connectors = _available_station(sample_stations[1], count=0)
    stale = replace(_available_station(sample_stations[1]), is_stale=True)
    for selector in (
        select_nearest_station,
        select_cheapest_station,
        select_fastest_station,
    ):
        assert selector((status_only,)) == (None, "no_available_station")
        assert selector((zero_connectors,)) == (None, "no_available_station")
        assert selector((stale,)) == (None, "no_available_station")


def test_cheapest_selector_compares_decimal_prices_before_distance(sample_stations):
    higher_price = _with_tariff(
        replace(_available_station(sample_stations[0]), distance_m=1),
        "EUR",
        "0.10000000000000001",
    )
    lower_price = _with_tariff(
        replace(_available_station(sample_stations[1]), distance_m=100),
        "EUR",
        "0.1",
    )
    unpriced = replace(
        _available_station(replace(sample_stations[0], provider_id="NL-TEST-UNPRICED")),
        distance_m=0,
    )
    stale = replace(
        _with_tariff(_available_station(sample_stations[0]), "EUR", "0.01"),
        is_stale=True,
    )

    station, reason = select_cheapest_station(
        (higher_price, unpriced, stale, lower_price)
    )

    assert station == lower_price
    assert reason == "lowest_price_per_kwh"


def test_cheapest_selector_uses_distance_then_stable_identity_on_ties(
    sample_stations,
):
    farther = _with_tariff(
        replace(_available_station(sample_stations[0]), distance_m=50),
        "EUR",
        "0.3",
    )
    first = _with_tariff(
        replace(_available_station(sample_stations[1]), distance_m=10),
        "EUR",
        "0.30",
    )
    second = _with_tariff(
        replace(
            _available_station(replace(sample_stations[0], provider_id="NL-TEST-003")),
            distance_m=10,
        ),
        "EUR",
        "0.300",
    )
    unknown_distance = _with_tariff(
        replace(_available_station(sample_stations[0]), distance_m=None),
        "EUR",
        "0.30",
    )

    station, reason = select_cheapest_station(
        (farther, unknown_distance, second, first)
    )

    assert station is not None
    assert station.provider_id == min(first.provider_id, second.provider_id)
    assert reason == "lowest_price_per_kwh"


def test_cheapest_selector_returns_reasons_for_missing_and_mixed_prices(
    sample_stations,
):
    no_price = _available_station(sample_stations[0])
    euro = _with_tariff(_available_station(sample_stations[1]), "EUR", "0.2")
    dollar = _with_tariff(_available_station(sample_stations[1]), "USD", "0.1")
    unpriced_other_currency = _with_tariff(
        _available_station(replace(sample_stations[0], provider_id="NL-TEST-UNPRICED")),
        "GBP",
        None,
    )
    ambiguous = replace(
        _with_tariff(_available_station(sample_stations[0]), "EUR", "0.1"),
        tariff_status=TariffStatus.AMBIGUOUS,
    )

    assert select_cheapest_station((no_price,)) == (None, "no_known_price")
    assert select_cheapest_station((ambiguous,)) == (None, "no_known_price")
    assert select_cheapest_station((euro, dollar, unpriced_other_currency)) == (
        None,
        "mixed_currencies",
    )


def test_fastest_selector_requires_positive_power_and_breaks_ties_stably(
    sample_stations,
):
    slower = replace(_available_station(sample_stations[0], power=100), distance_m=0)
    farther_fast = replace(
        _available_station(sample_stations[1], power=150), distance_m=25
    )
    closer_fast = replace(
        _available_station(
            replace(
                sample_stations[0],
                provider="eco_movement_be",
                provider_id="BE-FASTEST",
            ),
            power=150,
        ),
        distance_m=10,
    )
    same_distance_ndw = replace(
        _available_station(
            replace(sample_stations[1], provider_id="NL-FASTEST"),
            power=150,
        ),
        distance_m=10,
    )
    no_power = _available_station(sample_stations[0], power=0)
    unknown_power = _available_station(sample_stations[1])
    unknown_connector = replace(unknown_power.connectors[0], max_power_kw=None)
    unknown_power = replace(unknown_power, connectors=(unknown_connector,))

    station, reason = select_fastest_station(
        (
            slower,
            farther_fast,
            closer_fast,
            same_distance_ndw,
            no_power,
            unknown_power,
        )
    )

    assert station == closer_fast
    assert reason == "highest_maximum_power"
    assert select_fastest_station((no_power, unknown_power)) == (
        None,
        "no_positive_power",
    )
