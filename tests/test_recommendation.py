"""Tests for best-nearby charging station ranking."""

from dataclasses import replace
from decimal import Decimal

from custom_components.curbcharge.coordinator import recommend_station
from custom_components.curbcharge.models import ChargingTariff


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
