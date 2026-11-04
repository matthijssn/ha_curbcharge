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
    ConnectorStatus,
    ConnectorTypeId,
    EVSEStatus,
    OperatorReference,
    PersonalizationProfile,
    PersonalizationRankingPreset,
    PersonalizationUnknownDataPolicy,
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


def test_best_nearby_reports_known_price_when_another_price_is_missing(
    sample_stations,
):
    priced = _candidate(
        sample_stations[0],
        provider_id="NL-PRICED",
        distance=100,
        power=50,
        price="0.20",
    )
    unpriced = _candidate(
        sample_stations[1],
        provider_id="NL-UNPRICED",
        distance=10,
        power=50,
        price=None,
    )

    station, reason = recommend_station((unpriced, priced))

    assert station == priced
    assert reason == "known_price_per_kwh"


def test_best_nearby_reports_equal_price_distance_tiebreak(sample_stations):
    farther = _candidate(
        sample_stations[0],
        provider_id="NL-FARTHER",
        distance=20,
        power=50,
        price="0.20",
    )
    closer = _candidate(
        sample_stations[1],
        provider_id="NL-CLOSER",
        distance=10,
        power=50,
        price="0.20",
    )

    station, reason = recommend_station((farther, closer))

    assert station == closer
    assert reason == "shortest_distance_equal_prices"


def test_best_nearby_reports_power_tiebreak(sample_stations):
    slower = _candidate(
        sample_stations[0],
        provider_id="NL-SLOWER",
        distance=10,
        power=50,
        price="0.20",
    )
    faster = _candidate(
        sample_stations[1],
        provider_id="NL-FASTER",
        distance=10,
        power=150,
        price="0.20",
    )

    station, reason = recommend_station((slower, faster))

    assert station == faster
    assert reason == "higher_maximum_power"


def test_best_nearby_reports_stable_identity_tiebreaks(sample_stations):
    euro = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=10,
        power=50,
        price="0.20",
        currency="EUR",
    )
    dollar = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=10,
        power=50,
        price="0.10",
        currency="USD",
    )
    station, reason = recommend_station((dollar, euro))
    assert station == euro
    assert reason == "stable_station_id_mixed_currencies"

    known_euro = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=10,
        power=50,
        price="0.20",
    )
    equal_euro = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=10,
        power=50,
        price="0.20",
    )
    station, reason = recommend_station((equal_euro, known_euro))
    assert station == known_euro
    assert reason == "stable_station_id"


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


def _personalization_profile(**overrides):
    return PersonalizationProfile(enabled=True, **overrides)


def _identity_key(station):
    return (
        station.provider.casefold(),
        station.provider_id.casefold(),
        station.provider_id,
    )


def _with_connector_groups(station, *groups):
    return replace(station, connectors=tuple(groups))


def test_disabled_personalization_preserves_all_existing_results(sample_stations):
    stations = tuple(
        _candidate(
            sample_stations[index % 2],
            provider_id=f"NL-BASE-{index}",
            distance=25 + index,
            power=50 + index,
            price=f"0.{20 + index}",
        )
        for index in range(3)
    )

    existing = evaluate_recommendations(stations, stations, stations)
    disabled = evaluate_recommendations(
        stations,
        stations,
        stations,
        personalization_profile=PersonalizationProfile(),
    )

    assert disabled == existing


def test_personalization_presets_rank_candidates_and_keep_balanced_order(
    sample_stations,
):
    cheap = _candidate(
        sample_stations[0],
        provider_id="NL-CHEAP",
        distance=200,
        power=22,
        price="0.10",
        count=1,
    )
    nearest = _candidate(
        sample_stations[1],
        provider_id="NL-NEAREST",
        distance=10,
        power=22,
        price="0.30",
        count=1,
    )
    fastest = _candidate(
        sample_stations[0],
        provider_id="NL-FASTEST-PRESET",
        distance=100,
        power=150,
        price="0.40",
        count=1,
    )
    most_available = _candidate(
        sample_stations[1],
        provider_id="NL-MOST-AVAILABLE",
        distance=100,
        power=50,
        price="0.50",
        count=5,
    )
    stations = (cheap, nearest, fastest, most_available)

    expected = {
        PersonalizationRankingPreset.BALANCED: cheap,
        PersonalizationRankingPreset.LOWEST_PRICE: cheap,
        PersonalizationRankingPreset.SHORTEST_DISTANCE: nearest,
        PersonalizationRankingPreset.HIGHEST_POWER: fastest,
        PersonalizationRankingPreset.HIGHEST_AVAILABILITY: most_available,
    }
    for preset, winner in expected.items():
        results = evaluate_recommendations(
            stations,
            stations,
            personalization_profile=_personalization_profile(ranking_preset=preset),
        )
        assert results.best_nearby.station == winner
        evaluation = results.personalization_evaluations[_identity_key(winner)]
        assert evaluation.personalized_rank == 1
        assert evaluation.personalized_score == 100


def test_personalized_ranking_uses_stable_identity_for_ties(sample_stations):
    first = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=25,
        power=50,
        price="0.20",
    )
    second = _candidate(
        sample_stations[1],
        provider_id="NL-Z",
        distance=25,
        power=50,
        price="0.20",
    )
    profile = _personalization_profile(
        ranking_preset=PersonalizationRankingPreset.LOWEST_PRICE
    )

    forward = evaluate_recommendations(
        (first, second),
        (first, second),
        personalization_profile=profile,
    )
    reversed_input = evaluate_recommendations(
        (second, first),
        (second, first),
        personalization_profile=profile,
    )

    assert forward.best_nearby.station == reversed_input.best_nearby.station == first


def test_lowest_price_uses_balanced_order_when_prices_are_not_comparable(
    sample_stations,
):
    farther_eur = _candidate(
        sample_stations[0],
        provider_id="NL-FARTHER-EUR",
        distance=100,
        power=50,
        price="0.10",
        currency="EUR",
    )
    nearer_usd = _candidate(
        sample_stations[1],
        provider_id="NL-NEARER-USD",
        distance=10,
        power=50,
        price="0.10",
        currency="USD",
    )
    profile = _personalization_profile(
        ranking_preset=PersonalizationRankingPreset.LOWEST_PRICE
    )

    mixed = evaluate_recommendations(
        (farther_eur, nearer_usd),
        (farther_eur, nearer_usd),
        personalization_profile=profile,
    )
    unpriced = tuple(
        replace(station, tariff=None) for station in (farther_eur, nearer_usd)
    )
    unknown = evaluate_recommendations(
        unpriced,
        unpriced,
        personalization_profile=profile,
    )

    assert mixed.best_nearby.station == nearer_usd
    assert mixed.personalized_reason == "shortest_distance_mixed_currencies"
    assert unknown.best_nearby.station == unpriced[1]
    assert unknown.personalized_reason == "shortest_distance_price_unknown"


def test_preferred_operator_tier_uses_preset_order_within_the_tier(sample_stations):
    nonpreferred = _candidate(
        sample_stations[0],
        provider_id="NL-A",
        distance=20,
        power=50,
        price="0.10",
    )
    preferred_expensive = replace(
        _candidate(
            sample_stations[1],
            provider_id="NL-Z",
            distance=20,
            power=50,
            price="0.40",
        ),
        operator_id="preferred",
    )
    preferred_cheaper = replace(
        _candidate(
            sample_stations[0],
            provider_id="NL-Y",
            distance=30,
            power=50,
            price="0.30",
        ),
        operator_id="preferred",
    )
    profile = _personalization_profile(
        preferred_operators=(OperatorReference(nonpreferred.provider, "preferred"),),
        ranking_preset=PersonalizationRankingPreset.LOWEST_PRICE,
    )

    results = evaluate_recommendations(
        (nonpreferred, preferred_expensive, preferred_cheaper),
        (nonpreferred, preferred_expensive, preferred_cheaper),
        personalization_profile=profile,
    )

    assert results.best_nearby.station == preferred_cheaper
    assert results.personalization_evaluations[
        _identity_key(nonpreferred)
    ].fallback_eligible


def test_connector_compatibility_requires_one_available_group_to_meet_constraints(
    sample_stations,
):
    template = sample_stations[0].connectors[0]
    compatible_but_unavailable = replace(
        template,
        provider_connector_id="compatible-unavailable",
        connector_type_id=ConnectorTypeId.CCS_COMBO_2,
        max_power_kw=150,
        count=1,
        available_count=0,
    )
    available_but_incompatible = replace(
        template,
        provider_connector_id="available-incompatible",
        connector_type_id=ConnectorTypeId.TYPE_2,
        max_power_kw=250,
        count=1,
        available_count=1,
    )
    compatible_but_too_slow = replace(
        template,
        provider_connector_id="compatible-too-slow",
        connector_type_id=ConnectorTypeId.CCS_COMBO_2,
        max_power_kw=50,
        count=1,
        available_count=1,
    )
    station = _with_tariff(
        _with_connector_groups(
            replace(sample_stations[0], provider_id="NL-SPLIT-GROUPS"),
            compatible_but_unavailable,
            available_but_incompatible,
            compatible_but_too_slow,
        ),
        "EUR",
        "0.20",
    )
    compatible = _with_tariff(
        _with_connector_groups(
            replace(sample_stations[1], provider_id="NL-COMPATIBLE"),
            replace(
                template,
                provider_connector_id="compatible-available",
                connector_type_id=ConnectorTypeId.CCS_COMBO_2,
                max_power_kw=100,
                count=1,
                available_count=1,
            ),
            replace(
                template,
                provider_connector_id="unrelated-fast",
                connector_type_id=ConnectorTypeId.TYPE_2,
                max_power_kw=350,
                count=1,
                available_count=1,
            ),
        ),
        "EUR",
        "0.40",
    )
    compatible_faster = _with_tariff(
        _with_connector_groups(
            replace(sample_stations[0], provider_id="NL-COMPATIBLE-FASTER"),
            replace(
                template,
                provider_connector_id="compatible-fast",
                connector_type_id=ConnectorTypeId.CCS_COMBO_2,
                max_power_kw=150,
                count=1,
                available_count=1,
            ),
            replace(
                template,
                provider_connector_id="unrelated-slower",
                connector_type_id=ConnectorTypeId.TYPE_2,
                max_power_kw=200,
                count=1,
                available_count=1,
            ),
        ),
        "EUR",
        "0.45",
    )
    profile = _personalization_profile(
        connector_type_ids=(ConnectorTypeId.CCS_COMBO_2,),
        minimum_power_kw=100,
        maximum_price=Decimal("0.50"),
        maximum_price_currency="EUR",
        ranking_preset=PersonalizationRankingPreset.HIGHEST_POWER,
        allow_fallback=True,
        unknown_data_policy=PersonalizationUnknownDataPolicy.ALLOW_FALLBACK,
    )

    results = evaluate_recommendations(
        (station, compatible, compatible_faster),
        (station, compatible, compatible_faster),
        personalization_profile=profile,
    )

    assert results.best_nearby.station == compatible_faster
    rejected = results.personalization_evaluations[_identity_key(station)]
    assert not rejected.hard_eligible
    assert rejected.rejection_reason == "no_compatible_connector_group"
    selected = results.personalization_evaluations[_identity_key(compatible)]
    assert selected.selected_connector_group is not None
    assert selected.selected_connector_group.provider_connector_id == (
        "compatible-available"
    )
    faster = results.personalization_evaluations[_identity_key(compatible_faster)]
    assert faster.selected_connector_group is not None
    assert faster.selected_connector_group.provider_connector_id == "compatible-fast"


def test_strict_price_and_excluded_operator_are_never_fallback_candidates(
    sample_stations,
):
    template = sample_stations[0].connectors[0]

    def candidate(provider_id, currency, price, operator_id=None):
        station = _with_connector_groups(
            replace(
                _available_station(sample_stations[0]),
                provider_id=provider_id,
                operator_id=operator_id,
            ),
            replace(
                template,
                connector_type_id=ConnectorTypeId.TYPE_2,
                max_power_kw=50,
                count=1,
                available_count=1,
            ),
        )
        return (
            replace(station, tariff=None)
            if price is None
            else _with_tariff(station, currency, price)
        )

    candidates = (
        candidate("NL-OVER-CAP", "EUR", "0.60"),
        candidate("NL-WRONG-CURRENCY", "USD", "0.10"),
        candidate("NL-UNKNOWN-PRICE", "EUR", None),
        candidate("NL-EXCLUDED", "EUR", "0.10", "excluded"),
    )
    profile = _personalization_profile(
        connector_type_ids=(ConnectorTypeId.TYPE_2,),
        excluded_operators=(
            OperatorReference(sample_stations[0].provider, "excluded"),
        ),
        maximum_price=Decimal("0.50"),
        maximum_price_currency="EUR",
        allow_fallback=True,
        unknown_data_policy=PersonalizationUnknownDataPolicy.ALLOW_FALLBACK,
    )

    results = evaluate_recommendations(
        candidates,
        candidates,
        personalization_profile=profile,
    )

    assert results.best_nearby.station is None
    assert results.personalized_candidate_count == 0
    assert results.personalized_reason == "no_personalized_match"
    assert {
        results.personalization_evaluations[_identity_key(item)].rejection_reason
        for item in candidates
    } == {
        "maximum_price_exceeded",
        "maximum_price_currency_mismatch",
        "maximum_price_unknown",
        "excluded_operator",
    }


def test_fallback_is_used_only_when_no_full_preferred_match_exists(sample_stations):
    preferred = replace(
        _candidate(
            sample_stations[0],
            provider_id="NL-PREFERRED",
            distance=100,
            power=50,
            price="0.40",
        ),
        operator_id="preferred",
    )
    nonpreferred = replace(
        _candidate(
            sample_stations[1],
            provider_id="NL-NONPREFERRED",
            distance=1,
            power=50,
            price="0.10",
        ),
        operator_id="other",
    )
    profile = _personalization_profile(
        preferred_operators=(OperatorReference(preferred.provider, "preferred"),),
        allow_fallback=True,
    )

    with_full_match = evaluate_recommendations(
        (preferred, nonpreferred),
        (preferred, nonpreferred),
        personalization_profile=profile,
    )
    fallback_only = evaluate_recommendations(
        (nonpreferred,),
        (nonpreferred,),
        personalization_profile=profile,
    )
    no_fallback = evaluate_recommendations(
        (nonpreferred,),
        (nonpreferred,),
        personalization_profile=replace(profile, allow_fallback=False),
    )

    assert with_full_match.best_nearby.station == preferred
    assert not with_full_match.personalization_evaluations[
        _identity_key(nonpreferred)
    ].fallback_used
    assert fallback_only.best_nearby.station == nonpreferred
    assert fallback_only.personalization_evaluations[
        _identity_key(nonpreferred)
    ].fallback_used
    assert fallback_only.personalization_evaluations[
        _identity_key(nonpreferred)
    ].fallback_reasons == ("preferred_operator_not_matched",)
    assert no_fallback.best_nearby.station is None


def test_unknown_operator_follows_unknown_data_policy(sample_stations):
    unknown_operator = replace(
        _candidate(
            sample_stations[0],
            provider_id="NL-UNKNOWN-OPERATOR",
            distance=25,
            power=50,
            price=None,
        ),
        operator=None,
        operator_id=None,
    )
    profile = _personalization_profile(
        preferred_operators=(
            OperatorReference(unknown_operator.provider, "preferred"),
        ),
        allow_fallback=True,
        unknown_data_policy=PersonalizationUnknownDataPolicy.ALLOW_FALLBACK,
    )

    allowed = evaluate_recommendations(
        (unknown_operator,),
        (unknown_operator,),
        personalization_profile=profile,
    )
    excluded = evaluate_recommendations(
        (unknown_operator,),
        (unknown_operator,),
        personalization_profile=replace(
            profile,
            unknown_data_policy=PersonalizationUnknownDataPolicy.EXCLUDE,
        ),
    )
    exclusion_with_unknown_operator = evaluate_recommendations(
        (unknown_operator,),
        (unknown_operator,),
        personalization_profile=PersonalizationProfile(
            enabled=True,
            excluded_operators=(
                OperatorReference(unknown_operator.provider, "excluded"),
            ),
            allow_fallback=True,
            unknown_data_policy=PersonalizationUnknownDataPolicy.ALLOW_FALLBACK,
        ),
    )

    assert allowed.best_nearby.station == unknown_operator
    assert allowed.personalization_evaluations[
        _identity_key(unknown_operator)
    ].fallback_reasons == ("unknown_operator",)
    assert excluded.best_nearby.station is None
    assert exclusion_with_unknown_operator.best_nearby.station == unknown_operator


def test_unknown_connector_data_requires_explicit_fallback_and_availability(
    sample_stations,
):
    template = sample_stations[0].connectors[0]
    unknown_type = _with_connector_groups(
        _available_station(
            replace(sample_stations[0], provider_id="NL-UNKNOWN-TYPE"),
            power=50,
        ),
        replace(
            template,
            connector_type_id=None,
            available_count=1,
            max_power_kw=50,
        ),
    )
    unknown_availability = _with_connector_groups(
        replace(
            sample_stations[1],
            provider_id="NL-UNKNOWN-AVAILABILITY",
            evse_status=EVSEStatus.AVAILABLE,
        ),
        replace(
            template,
            connector_type_id=ConnectorTypeId.TYPE_2,
            status=ConnectorStatus.UNKNOWN,
            available_count=None,
            max_power_kw=50,
        ),
    )
    unknown_power = _with_connector_groups(
        replace(sample_stations[0], provider_id="NL-UNKNOWN-POWER"),
        replace(
            template,
            connector_type_id=ConnectorTypeId.TYPE_2,
            status=ConnectorStatus.AVAILABLE,
            available_count=1,
            max_power_kw=None,
        ),
    )
    profile = _personalization_profile(
        connector_type_ids=(ConnectorTypeId.TYPE_2,),
        minimum_power_kw=22,
        allow_fallback=True,
        unknown_data_policy=PersonalizationUnknownDataPolicy.ALLOW_FALLBACK,
    )

    fallback = evaluate_recommendations(
        (unknown_type,),
        (unknown_type,),
        personalization_profile=profile,
    )
    fallback_power = evaluate_recommendations(
        (unknown_power,),
        (unknown_power,),
        personalization_profile=profile,
    )
    highest_power_unknown = evaluate_recommendations(
        (unknown_power,),
        (unknown_power,),
        personalization_profile=replace(
            profile,
            minimum_power_kw=None,
            ranking_preset=PersonalizationRankingPreset.HIGHEST_POWER,
        ),
    )
    station_power_unknown = evaluate_recommendations(
        (unknown_power,),
        (unknown_power,),
        personalization_profile=replace(
            profile,
            connector_type_ids=(),
            minimum_power_kw=None,
            ranking_preset=PersonalizationRankingPreset.HIGHEST_POWER,
        ),
    )
    no_known_group_availability = evaluate_recommendations(
        (unknown_availability,),
        (unknown_availability,),
        personalization_profile=profile,
    )
    conservative = evaluate_recommendations(
        (unknown_type,),
        (unknown_type,),
        personalization_profile=replace(
            profile,
            unknown_data_policy=PersonalizationUnknownDataPolicy.EXCLUDE,
        ),
    )

    assert fallback.best_nearby.station == unknown_type
    assert fallback.personalization_evaluations[
        _identity_key(unknown_type)
    ].fallback_reasons == ("unknown_connector_type",)
    assert fallback_power.best_nearby.station == unknown_power
    assert fallback_power.personalization_evaluations[
        _identity_key(unknown_power)
    ].fallback_reasons == ("unknown_power",)
    assert highest_power_unknown.best_nearby.station == unknown_power
    assert highest_power_unknown.personalization_evaluations[
        _identity_key(unknown_power)
    ].fallback_reasons == ("unknown_power",)
    assert station_power_unknown.best_nearby.station == unknown_power
    assert station_power_unknown.personalization_evaluations[
        _identity_key(unknown_power)
    ].fallback_reasons == ("unknown_power",)
    assert no_known_group_availability.best_nearby.station is None
    assert conservative.best_nearby.station is None


def test_personalization_does_not_change_nearest_cheapest_or_fastest_selectors(
    sample_stations,
):
    nearest = replace(
        _candidate(
            sample_stations[0],
            provider_id="NL-EXCLUDED-NEAREST",
            distance=1,
            power=50,
            price="0.10",
        ),
        operator_id="excluded",
    )
    other = _candidate(
        sample_stations[1],
        provider_id="NL-OTHER",
        distance=50,
        power=100,
        price="0.20",
    )
    expected = evaluate_recommendations((nearest, other), (nearest, other))
    personalized = evaluate_recommendations(
        (nearest, other),
        (nearest, other),
        personalization_profile=_personalization_profile(
            excluded_operators=(OperatorReference(nearest.provider, "excluded"),)
        ),
    )

    assert personalized.best_nearby.station == other
    assert personalized.nearest_available == expected.nearest_available
    assert personalized.cheapest_available == expected.cheapest_available
    assert personalized.fastest_available == expected.fastest_available
