"""Tests for best-nearby charging station ranking."""

from dataclasses import replace
from decimal import Decimal

from custom_components.curbcharge.coordinator import (
    evaluate_recommendations,
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


def _candidate(
    station,
    *,
    provider_id,
    distance,
    power,
    price,
    count=1,
    currency="EUR",
):
    station = replace(station, provider_id=provider_id, distance_m=distance)
    station = _available_station(station, count=count, power=power)
    return (
        replace(station, tariff=None)
        if price is None
        else _with_tariff(station, currency, price)
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
    status_only_results = evaluate_recommendations((status_only,), (status_only,))
    assert status_only_results.best_nearby.best_nearby_rank == 1
    assert status_only_results.best_nearby.best_nearby_rank_score == 100
    assert status_only_results.best_nearby.best_nearby_candidate_count == 1
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


def test_cheapest_selector_uses_connector_count_then_power_before_identity(
    sample_stations,
):
    fewer_connectors = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=0,
        count=1,
        power=50,
        price="0.20",
    )
    more_connectors = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=0,
        count=2,
        power=50,
        price="0.20",
    )
    station, _reason = select_cheapest_station((fewer_connectors, more_connectors))
    assert station == more_connectors

    lower_power = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=0,
        power=50,
        price="0.20",
    )
    higher_power = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=0,
        power=150,
        price="0.20",
    )
    station, _reason = select_cheapest_station((lower_power, higher_power))
    assert station == higher_power


def test_cheapest_selector_treats_zero_price_and_distance_as_known(sample_stations):
    zero_price = _candidate(
        sample_stations[0],
        provider_id="NL-Z",
        distance=10,
        power=50,
        price="0",
    )
    positive_price = _candidate(
        sample_stations[1],
        provider_id="NL-A",
        distance=0,
        power=50,
        price="0.01",
    )
    station, _reason = select_cheapest_station((positive_price, zero_price))
    assert station == zero_price

    zero_distance = _candidate(
        sample_stations[0],
        provider_id="NL-Z",
        distance=0,
        power=50,
        price="0.20",
    )
    positive_distance = _candidate(
        sample_stations[1],
        provider_id="NL-A",
        distance=1,
        power=50,
        price="0.20",
    )
    station, _reason = select_cheapest_station((positive_distance, zero_distance))
    assert station == zero_distance


def test_fastest_selector_uses_connectors_and_comparable_price_tiebreaks(
    sample_stations,
):
    fewer_connectors = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=10,
        power=150,
        price="0.10",
    )
    more_connectors = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=10,
        count=2,
        power=150,
        price="0.90",
    )
    station, _reason = select_fastest_station((fewer_connectors, more_connectors))
    assert station == more_connectors

    cheaper = _candidate(
        sample_stations[0],
        provider_id="NL-Z",
        distance=10,
        power=150,
        price="0",
    )
    costlier = _candidate(
        sample_stations[1],
        provider_id="NL-A",
        distance=10,
        power=150,
        price="0.10",
    )
    station, _reason = select_fastest_station((costlier, cheaper))
    assert station == cheaper


def test_fastest_selector_skips_mixed_currency_and_keeps_unknown_price_null(
    sample_stations,
):
    usd = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=10,
        power=150,
        price="0.50",
        currency="USD",
    )
    euro = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=10,
        power=150,
        price="0.01",
    )
    station, _reason = select_fastest_station((euro, usd))
    assert station == usd

    unknown_price = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=10,
        power=150,
        price=None,
    )
    known_price = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=10,
        power=150,
        price="0.20",
    )
    station, _reason = select_fastest_station((unknown_price, known_price))
    assert station == known_price


def test_recommendation_scores_are_candidate_relative_and_expose_unknowns(
    sample_stations,
):
    cheapest = _candidate(
        sample_stations[0],
        provider_id="NL-CHEAP",
        distance=100,
        power=50,
        price="0.10",
    )
    middle = _candidate(
        sample_stations[1],
        provider_id="NL-MIDDLE",
        distance=50,
        power=50,
        price="0.20",
    )
    unpriced = _candidate(
        sample_stations[0],
        provider_id="NL-UNPRICED",
        distance=0,
        power=50,
        price=None,
    )
    stations = (cheapest, middle, unpriced)

    results = evaluate_recommendations(stations, stations, stations)

    assert results.best_nearby.station == cheapest
    assert results.best_nearby.reason == "lowest_price_per_kwh"
    assert results.best_nearby.best_nearby_candidate_count == 3
    assert results.best_nearby.best_nearby_rank == 1
    assert results.best_nearby.best_nearby_rank_score == 100
    assert results.nearest_available.station == unpriced
    assert results.nearest_available.best_nearby_rank == 3
    assert results.nearest_available.best_nearby_rank_score == 100 / 3
    middle_identity = (
        middle.provider.casefold(),
        middle.provider_id.casefold(),
        middle.provider_id,
    )
    assert results.best_nearby_ranks[middle_identity] == (2, 200 / 3)
    assert results.cheapest_available.station == cheapest
    assert results.cheapest_available.best_nearby_rank == 1
    assert results.fastest_available.best_nearby_candidate_count == 3

    empty = evaluate_recommendations((), ())
    assert empty.best_nearby.station is None
    assert empty.best_nearby.best_nearby_candidate_count == 0
    assert empty.best_nearby.best_nearby_rank is None
    assert empty.best_nearby.best_nearby_rank_score is None
