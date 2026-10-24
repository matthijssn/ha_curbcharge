"""Tests for the NDW DOT-NL API client."""

from __future__ import annotations

import copy
import gzip
import json
import logging
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import aiohttp
import pytest

import custom_components.curbcharge.api as api
from custom_components.curbcharge.api import (
    NdwDotNlProvider,
    _parse_tariff,
    _station_from_feature,
    _with_station_tariff,
    create_provider,
)
from custom_components.curbcharge.belgium import BelgiumChargingProvider
from custom_components.curbcharge.const import (
    DOMAIN,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.models import (
    ChargingTariff,
    ConnectorStatus,
    DiscoveryFilterSettings,
    FavouriteStationReference,
    ProviderTimestampSource,
    TariffStatus,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
    retry_after_seconds,
)


class FakeResponse:
    """Minimal aiohttp response context manager."""

    def __init__(
        self,
        payload: Any,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.body = payload if raw else json.dumps(payload).encode()

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    """Record requests and return preloaded responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str] | None]] = []

    def get(
        self, url: str, *, params: dict[str, str] | None = None, timeout: Any = None
    ) -> FakeResponse:
        self.requests.append((url, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _without_tariffs(payload: dict[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(payload)
    for feature in payload["features"]:
        for availability in feature["properties"].get("availabilities") or []:
            availability["tariff_ids"] = []
    return payload


@pytest.mark.asyncio
async def test_get_stations_filters_by_radius_and_limit(sample_geojson):
    session = FakeSession([FakeResponse(_without_tariffs(sample_geojson))])
    provider = NdwDotNlProvider(session)

    result = await provider.async_get_stations(52.0, 5.0, 100, 2)
    stations = result.nearby

    assert [station.provider_id for station in stations] == [
        "NL-TEST-001",
        "NL-TEST-002",
    ]
    assert result.favourites == ()
    assert stations[0].distance_m == 0
    assert stations[0].available_connectors == 2
    assert stations[0].total_connectors == 4
    assert stations[0].unavailable_connectors == 2
    assert stations[0].occupied_connectors is None
    assert stations[0].max_power_kw == 50
    assert stations[0].connectors[0].status is ConnectorStatus.UNKNOWN
    assert stations[0].tariff is None
    assert stations[0].country_code == "NL"
    assert stations[0].location is not None
    assert stations[0].location.provider_id == stations[0].provider_id
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_connector_filters_run_before_cap_and_tariff_lookup(
    sample_geojson, monkeypatch
):
    payload = copy.deepcopy(sample_geojson)
    first, second, third = payload["features"]
    first_t2 = next(
        group
        for group in first["properties"]["availabilities"]
        if group["connector_type"] == "IEC_62196_T2"
    )
    first_t2["available"] = 0
    first_t2["tariff_ids"] = ["excluded-tariff"]
    first_chademo = next(
        group
        for group in first["properties"]["availabilities"]
        if group["connector_type"] == "CHADEMO"
    )
    first_chademo["available"] = 1
    second_group = second["properties"]["availabilities"][0]
    second_group["available"] = 1
    second_group["tariff_ids"] = ["selected-tariff"]
    third_group = copy.deepcopy(second_group)
    third_group["tariff_ids"] = ["farther-tariff"]
    third["properties"]["availabilities"] = [third_group]

    session = FakeSession([FakeResponse(payload)])
    provider = NdwDotNlProvider(session)
    tariff_requests: list[set[str]] = []

    async def capture_tariff_requests(
        tariff_ids: set[str],
    ) -> dict[str, ChargingTariff | None]:
        tariff_requests.append(set(tariff_ids))
        return {}

    monkeypatch.setattr(provider, "_async_get_tariffs", capture_tariff_requests)
    result = await provider.async_get_stations(
        52.0,
        5.0,
        500,
        1,
        discovery_filters=DiscoveryFilterSettings(
            connector_types=("IEC_62196_T2",),
            minimum_power_kw=11.0,
            require_available=True,
        ),
    )

    assert [station.provider_id for station in result.nearby] == ["NL-TEST-002"]
    assert tariff_requests == [{"selected-tariff"}]
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_connector_filter_preserves_radius_limit(sample_geojson):
    payload = copy.deepcopy(sample_geojson)
    second_group = payload["features"][1]["properties"]["availabilities"][0]
    second_group["available"] = 1
    third_group = copy.deepcopy(second_group)
    payload["features"][2]["properties"]["availabilities"] = [third_group]

    session = FakeSession([FakeResponse(_without_tariffs(payload))])
    provider = NdwDotNlProvider(session)
    result = await provider.async_get_stations(
        52.0,
        5.0,
        100,
        10,
        discovery_filters=DiscoveryFilterSettings(
            connector_types=("IEC_62196_T2",),
            minimum_power_kw=11.0,
            require_available=True,
        ),
    )

    assert [station.provider_id for station in result.nearby] == [
        "NL-TEST-001",
        "NL-TEST-002",
    ]
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_filtered_candidates_break_distance_ties_by_station_id(sample_geojson):
    payload = copy.deepcopy(sample_geojson)
    first, second, third = payload["features"]
    first_t2 = next(
        group
        for group in first["properties"]["availabilities"]
        if group["connector_type"] == "IEC_62196_T2"
    )
    first_t2["available"] = 0
    next(
        group
        for group in first["properties"]["availabilities"]
        if group["connector_type"] == "CHADEMO"
    )["available"] = 1
    second_group = second["properties"]["availabilities"][0]
    second_group["available"] = 1
    third["geometry"]["coordinates"] = second["geometry"]["coordinates"]
    third["properties"]["availabilities"] = [copy.deepcopy(second_group)]

    session = FakeSession([FakeResponse(_without_tariffs(payload))])
    provider = NdwDotNlProvider(session)
    result = await provider.async_get_stations(
        52.0,
        5.0,
        500,
        1,
        discovery_filters=DiscoveryFilterSettings(
            connector_types=("IEC_62196_T2",),
            minimum_power_kw=11.0,
            require_available=True,
        ),
    )

    assert [station.provider_id for station in result.nearby] == ["NL-TEST-002"]


@pytest.mark.asyncio
async def test_unknown_availability_is_filtered_before_station_cap(sample_geojson):
    payload = copy.deepcopy(sample_geojson)
    payload["features"][0]["properties"]["availabilities"] = None
    session = FakeSession([FakeResponse(_without_tariffs(payload))])
    provider = NdwDotNlProvider(session)

    result = await provider.async_get_stations(52.0, 5.0, 500, 1, include_unknown=False)

    assert [station.provider_id for station in result.nearby] == ["NL-TEST-002"]
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_get_stations_recalculates_radius_and_limit_for_moved_center(
    sample_geojson,
):
    center = (52.0, 5.001)
    scenarios = [
        (100, 2, ["NL-TEST-002", "NL-TEST-001"]),
        (60, 10, ["NL-TEST-002"]),
    ]

    for radius_m, max_stations, expected_ids in scenarios:
        session = FakeSession([FakeResponse(_without_tariffs(sample_geojson))])
        provider = NdwDotNlProvider(session)

        stations = (
            await provider.async_get_stations(
                *center, radius_m=radius_m, max_stations=max_stations
            )
        ).nearby

        assert [station.provider_id for station in stations] == expected_ids
        assert stations[0].distance_m == 0
        assert session.requests[0][1] == {"bbox": api._bounding_box(*center, radius_m)}
        assert session.requests[0][1] != {
            "bbox": api._bounding_box(52.0, 5.0, radius_m)
        }


@pytest.mark.asyncio
async def test_valid_empty_response_is_empty(sample_geojson):
    session = FakeSession([FakeResponse({"type": "FeatureCollection", "features": []})])
    provider = NdwDotNlProvider(session)

    result = await provider.async_get_stations(52.0, 5.0, 500, 10)
    assert result.nearby == ()
    assert result.favourites == ()


@pytest.mark.asyncio
async def test_favourite_outside_radius_is_fetched_without_displacing_nearby(
    sample_geojson,
):
    first_page = copy.deepcopy(sample_geojson)
    favourite_feature = first_page["features"].pop()
    session = FakeSession(
        [
            FakeResponse(_without_tariffs(first_page)),
            FakeResponse(
                _without_tariffs(
                    {"type": "FeatureCollection", "features": [favourite_feature]}
                )
            ),
        ]
    )
    provider = NdwDotNlProvider(session)
    favourite = FavouriteStationReference(
        provider=provider.provider_id,
        provider_id="NL-TEST-003",
        name="Teststraat 3",
        latitude=52.0,
        longitude=5.002,
    )

    result = await provider.async_get_stations(52.0, 5.0, 100, 2, (favourite,))

    assert [station.provider_id for station in result.nearby] == [
        "NL-TEST-001",
        "NL-TEST-002",
    ]
    assert [station.provider_id for station in result.favourites] == ["NL-TEST-003"]
    assert result.favourites[0].distance_m > 100
    assert len(session.requests) == 2


@pytest.mark.asyncio
async def test_favourite_beyond_nearby_cap_reuses_the_normal_geojson_page(
    sample_geojson,
):
    session = FakeSession([FakeResponse(_without_tariffs(sample_geojson))])
    provider = NdwDotNlProvider(session)
    favourite = FavouriteStationReference(
        provider=provider.provider_id,
        provider_id="NL-TEST-002",
        name="Teststraat 2",
        latitude=52.0,
        longitude=5.001,
    )

    result = await provider.async_get_stations(52.0, 5.0, 500, 1, (favourite,))

    assert [station.provider_id for station in result.nearby] == ["NL-TEST-001"]
    assert [station.provider_id for station in result.favourites] == ["NL-TEST-002"]
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_favourite_already_in_nearby_results_is_not_duplicated(sample_geojson):
    session = FakeSession([FakeResponse(_without_tariffs(sample_geojson))])
    provider = NdwDotNlProvider(session)
    favourite = FavouriteStationReference(
        provider=provider.provider_id,
        provider_id="NL-TEST-001",
        name="Teststraat 1",
        latitude=52.0,
        longitude=5.0,
    )

    result = await provider.async_get_stations(52.0, 5.0, 500, 10, (favourite,))

    assert "NL-TEST-001" in {station.provider_id for station in result.nearby}
    assert result.favourites == ()
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_provider_rejects_favourite_from_a_different_provider(sample_geojson):
    provider = NdwDotNlProvider(
        FakeSession([FakeResponse(_without_tariffs(sample_geojson))])
    )
    favourite = FavouriteStationReference(
        provider=PROVIDER_ECO_MOVEMENT_BE,
        provider_id="station-1",
        name="Station",
        latitude=52.0,
        longitude=5.0,
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(52.0, 5.0, 500, 10, (favourite,))


@pytest.mark.asyncio
async def test_ndw_rate_limit_deferral_retries_waiting_requests(monkeypatch):
    limiter = api.NdwRequestLimiter()
    limiter._last_request = 0.0
    clock = [0.0]
    sleeps = 0

    def monotonic() -> float:
        return clock[0]

    async def sleep(delay: float) -> None:
        nonlocal sleeps
        sleeps += 1
        clock[0] += delay + 0.001
        if sleeps == 1:
            limiter.defer_requests(1)

    monkeypatch.setattr(api.time, "monotonic", monotonic)
    monkeypatch.setattr(api.asyncio, "sleep", sleep)

    await limiter.async_wait()

    assert sleeps == 2
    assert limiter._last_request == clock[0]


def test_shared_ndw_limiter_rejects_invalid_hass_data(hass):
    hass.data.setdefault(DOMAIN, {})[api._NDW_REQUEST_LIMITER_DATA_KEY] = object()

    with pytest.raises(RuntimeError, match="NDW request limiter"):
        api._shared_ndw_request_limiter(hass)


def test_ndw_provider_exposes_stable_country_identity():
    provider = NdwDotNlProvider(FakeSession([]))

    assert provider.provider_id == "ndw_dot_nl"
    assert provider.country_code == "NL"


def test_ndw_providers_share_request_limiter_per_home_assistant(hass):
    first = create_provider(PROVIDER_NDW_DOT_NL, FakeSession([]), hass)
    second = create_provider(PROVIDER_NDW_DOT_NL, FakeSession([]), hass)

    assert isinstance(first, NdwDotNlProvider)
    assert isinstance(second, NdwDotNlProvider)
    assert first._request_limiter is second._request_limiter


@pytest.mark.asyncio
async def test_pagination_follows_the_documented_next_link(sample_geojson):
    features = sample_geojson["features"]
    link = (
        "<https://dotnl.ndw.nu/api/rest/geojson/dynamic-road-status/"
        'charge-point-data/v1/features?cursor=next>; rel="next"'
    )
    session = FakeSession(
        [
            FakeResponse(
                {"type": "FeatureCollection", "features": features[:1]},
                headers={"X-Total-Count": "2", "Link": link},
            ),
            FakeResponse(
                {"type": "FeatureCollection", "features": features[1:2]},
            ),
        ]
    )
    provider = NdwDotNlProvider(session)

    await provider.async_validate_connection(52.0, 5.0, 500)

    assert len(session.requests) == 2
    assert session.requests[1][1] is None


@pytest.mark.asyncio
async def test_malformed_response_fails_explicitly():
    provider = NdwDotNlProvider(FakeSession([FakeResponse({"features": []})]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(52.0, 5.0, 500)


@pytest.mark.asyncio
async def test_invalid_json_fails_explicitly():
    provider = NdwDotNlProvider(FakeSession([FakeResponse(b"{", raw=True)]))

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(52.0, 5.0, 500)


@pytest.mark.asyncio
async def test_connection_failure_is_translated():
    provider = NdwDotNlProvider(
        FakeSession([aiohttp.ClientConnectionError("no connection")])
    )

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(52.0, 5.0, 500)


@pytest.mark.asyncio
async def test_authentication_failure_is_translated():
    provider = NdwDotNlProvider(FakeSession([FakeResponse({}, status=401)]))

    with pytest.raises(AuthenticationError):
        await provider.async_validate_connection(52.0, 5.0, 500)


@pytest.mark.asyncio
async def test_rate_limit_preserves_retry_after():
    provider = NdwDotNlProvider(
        FakeSession([FakeResponse({}, status=429, headers={"Retry-After": "120"})])
    )

    with pytest.raises(ProviderRateLimitError) as error:
        await provider.async_validate_connection(52.0, 5.0, 500)

    assert error.value.retry_after == 120


def test_tariff_prices_remain_decimal():
    tariff = _parse_tariff(
        {
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": None,
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": 0.257,
                            "vat": 21.0,
                            "step_size": 1,
                        },
                        {
                            "type": "FLAT",
                            "price": 0.1,
                            "vat": 21.0,
                            "step_size": 1,
                        },
                        {
                            "type": "TIME",
                            "price": 0.05,
                            "vat": 21.0,
                            "step_size": 60,
                        },
                    ],
                }
            ],
        }
    )

    assert tariff is not None
    assert tariff.price_per_kwh == Decimal("0.31097")
    assert tariff.session_fee == Decimal("0.121")
    assert tariff.price_per_minute == Decimal("0.0605")
    assert tariff.vat_percentage == Decimal("21.0")


def test_connector_group_ids_survive_new_groups_before_existing_groups(
    sample_geojson,
):
    import copy

    feature = copy.deepcopy(sample_geojson["features"][0])
    original_ids = {
        connector.provider_connector_id
        for connector in _station_from_feature(feature, 52.0, 5.0).connectors
    }
    feature["properties"]["availabilities"].append(
        {
            "available": 1,
            "connector_format": "SOCKET",
            "connector_type": "DOMESTIC_A",
            "power_max": 1000.0,
            "power_type": "AC1",
            "total": 1,
            "tariff_ids": [],
        }
    )

    updated_ids = {
        connector.provider_connector_id
        for connector in _station_from_feature(feature, 52.0, 5.0).connectors
    }

    assert original_ids < updated_ids


def test_restricted_tariff_price_is_not_assumed():
    tariff = _parse_tariff(
        {
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": {"day_of_week": ["MONDAY"]},
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": 0.25,
                            "vat": 21.0,
                            "step_size": 1,
                        }
                    ],
                }
            ],
        }
    )

    assert tariff is not None
    assert tariff.price_per_kwh is None


def test_component_without_vat_is_not_presented_as_gross_price():
    tariff = _parse_tariff(
        {
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": None,
                    "price_components": [
                        {"type": "ENERGY", "price": 0.25, "step_size": 1}
                    ],
                }
            ],
        }
    )

    assert tariff.price_per_kwh is None


def test_tax_included_component_is_not_taxed_twice():
    tariff = _parse_tariff(
        {
            "currency": "EUR",
            "tax_included": "YES",
            "elements": [
                {
                    "restrictions": None,
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": 0.35,
                            "vat": 21.0,
                            "step_size": 1,
                        }
                    ],
                }
            ],
        }
    )

    assert tariff is not None
    assert tariff.price_per_kwh == Decimal("0.35")


@pytest.mark.asyncio
async def test_tariff_refresh_runs_in_executor_and_refreshes_cached_tariffs(
    monkeypatch,
):
    parser_threads: list[int] = []
    parse_tariff_feed = api._parse_tariff_feed

    def record_parser_thread(
        body: bytes, lookup_ids: set[str], now: datetime
    ) -> dict[str, ChargingTariff | None]:
        parser_threads.append(threading.get_ident())
        return parse_tariff_feed(body, lookup_ids, now)

    monkeypatch.setattr(api, "_parse_tariff_feed", record_parser_thread)
    records = [
        {
            "id": tariff_id,
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": None,
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": price,
                            "vat": 21.0,
                            "step_size": 1,
                        }
                    ],
                }
            ],
        }
        for tariff_id, price in (("old", 0.4), ("new", 0.5))
    ]
    body = gzip.compress(json.dumps(records).encode())
    provider = NdwDotNlProvider(FakeSession([FakeResponse(body, raw=True)]))
    provider._tariff_cache = {
        "old": ChargingTariff("EUR", Decimal("0.121"), None, None, None, "NDW DOT-NL")
    }
    provider._cached_tariff_ids = {"old"}
    provider._tariff_cache_updated = datetime.now(UTC)

    tariffs = await provider._async_get_tariffs({"new"})

    assert tariffs["new"] is not None
    assert tariffs["new"].price_per_kwh == Decimal("0.605")
    assert provider._tariff_cache["old"].price_per_kwh == Decimal("0.484")
    assert parser_threads and parser_threads[0] != threading.get_ident()


def test_mixed_currency_tariffs_are_not_combined(sample_stations):
    from dataclasses import replace

    station = sample_stations[0]
    connector = replace(station.connectors[0], tariff_ids=("eur", "usd"))
    station = replace(station, connectors=(connector,))
    tariffs = {
        "eur": ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW"),
        "usd": ChargingTariff("USD", Decimal("0.18"), None, None, None, "NDW"),
    }

    assert _with_station_tariff(station, tariffs).tariff is None


def test_connector_group_ids_are_stable_when_a_new_group_is_discovered(
    sample_geojson,
):
    feature = copy.deepcopy(sample_geojson["features"][0])
    original_ids = {
        connector.provider_connector_id
        for connector in _station_from_feature(feature, 52.0, 5.0).connectors
    }
    feature["properties"]["availabilities"].append(
        {
            "available": 1,
            "connector_format": "SOCKET",
            "connector_type": "DOMESTIC_A",
            "power_max": 1000.0,
            "power_type": "AC1",
            "total": 1,
            "tariff_ids": [],
        }
    )

    updated_ids = {
        connector.provider_connector_id
        for connector in _station_from_feature(feature, 52.0, 5.0).connectors
    }

    assert original_ids < updated_ids


@pytest.mark.parametrize("remove_timestamp", [False, True])
def test_missing_ndw_timestamp_preserves_valid_availability(
    sample_geojson, remove_timestamp
):
    feature = copy.deepcopy(sample_geojson["features"][0])
    if remove_timestamp:
        feature["properties"].pop("last_updated")
    else:
        feature["properties"]["last_updated"] = None

    station = _station_from_feature(feature, 52.0, 5.0)

    assert station.last_updated is None
    assert station.last_updated_source is ProviderTimestampSource.NDW_FEATURE
    assert station.availability_reported
    assert station.available_connectors == 2


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"type": "Feature", "features": []},
        {"type": "FeatureCollection", "features": {}},
        {"type": "FeatureCollection", "features": [{"type": "Point"}]},
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": "", "geometry": {}, "properties": {}}
            ],
        },
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": "station", "geometry": [], "properties": {}}
            ],
        },
    ],
)
def test_feature_collection_rejects_malformed_payloads(payload):
    with pytest.raises(InvalidProviderResponseError):
        api._feature_collection(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("type",), "Point"),
        (("id",), ""),
        (("geometry", "type"), "Polygon"),
        (("geometry", "coordinates"), [5]),
        (("geometry", "coordinates"), [181, 52]),
        (("geometry", "coordinates"), [5, float("nan")]),
        (("properties", "address"), None),
        (("properties", "cpo_id"), ""),
        (("properties", "open"), 1),
        (("properties", "operator_name"), 5),
        (("properties", "last_updated"), "not-a-timestamp"),
        (("properties", "last_updated"), "2026-10-11T12:00:00"),
        (("properties", "availabilities"), {}),
        (("properties", "availabilities", 0, "available"), True),
        (("properties", "availabilities", 0, "available"), 100),
        (("properties", "availabilities", 0, "total"), -1),
        (("properties", "availabilities", 0, "connector_type"), ""),
        (("properties", "availabilities", 0, "power_max"), -1),
        (("properties", "availabilities", 0, "tariff_ids"), ["valid", 1]),
    ],
)
def test_station_parser_rejects_invalid_fields(sample_geojson, path, value):
    feature = copy.deepcopy(sample_geojson["features"][0])
    target = feature
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(InvalidProviderResponseError):
        _station_from_feature(feature, 52.0, 5.0)


def test_bounding_box_rejects_out_of_bounds_searches():
    for latitude, longitude, radius_m in (
        (91, 0, 100),
        (0, 181, 100),
        (0, 179.999, 1000),
        (0, 0, 100_000),
    ):
        with pytest.raises(UnsupportedLocationError):
            api._bounding_box(latitude, longitude, radius_m)

    assert api._bounding_box(90, 0, 100)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("120", 120),
        ("-1", 0),
        ("not a date", None),
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0),
    ],
)
def test_retry_after_accepts_delta_seconds_or_http_dates(value, expected):
    assert retry_after_seconds(value) == expected


def test_pagination_links_must_remain_on_the_provider_endpoint():
    current_url = api.PROVIDER_API_URL
    assert api._next_page_url(None, current_url) is None
    next_url = f"{current_url}?cursor=next"
    assert api._next_page_url(f"<{next_url}>; rel=next", current_url) == next_url

    previous = "<https://example.com/page>; rel=previous"
    assert api._next_page_url(previous, current_url) is None
    with pytest.raises(InvalidProviderResponseError):
        api._next_page_url(
            "<https://example.com/page>; rel=next",
            current_url,
        )


@pytest.mark.asyncio
async def test_pagination_rejects_invalid_metadata_missing_pages_loops_and_duplicates(
    sample_geojson,
):
    features = sample_geojson["features"]
    next_url = f"{api.PROVIDER_API_URL}?cursor=next"
    next_link = f'<{next_url}>; rel="next"'
    cases = (
        [
            FakeResponse(
                {"type": "FeatureCollection", "features": []},
                headers={"X-Total-Count": "invalid"},
            )
        ],
        [
            FakeResponse(
                {"type": "FeatureCollection", "features": features[:1]},
                headers={"X-Total-Count": "2", "Link": next_link},
            ),
            FakeResponse({"type": "FeatureCollection", "features": []}),
        ],
        [
            FakeResponse(
                {"type": "FeatureCollection", "features": features[:1]},
                headers={"Link": next_link},
            ),
            FakeResponse(
                {"type": "FeatureCollection", "features": features[:1]},
                headers={"Link": next_link},
            ),
        ],
        [
            FakeResponse(
                {
                    "type": "FeatureCollection",
                    "features": [features[0], features[0]],
                }
            )
        ],
    )

    for responses in cases:
        provider = NdwDotNlProvider(FakeSession(responses))
        with pytest.raises(InvalidProviderResponseError):
            await provider.async_validate_connection(52.0, 5.0, 500)


def test_tariff_parser_rejects_invalid_records():
    records = (
        {"currency": "EU", "elements": []},
        {"currency": "EUR", "elements": {}},
        {"currency": "EUR", "tax_included": "MAYBE", "elements": []},
        {"currency": "EUR", "elements": [None]},
        {"currency": "EUR", "elements": [{"price_components": None}]},
        {"currency": "EUR", "elements": [{"price_components": [None]}]},
        {
            "currency": "EUR",
            "elements": [{"price_components": [{"type": "", "price": 1, "vat": 21}]}],
        },
    )
    for record in records:
        with pytest.raises(InvalidProviderResponseError):
            _parse_tariff(record)


def test_inactive_tariffs_and_unambiguous_descriptions_are_handled():
    component = {"type": "ENERGY", "price": 0.25, "vat": 21.0, "step_size": 1}
    for bounds in (
        {"start_date_time": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        {"end_date_time": (datetime.now(UTC) - timedelta(days=1)).isoformat()},
    ):
        tariff = _parse_tariff(
            {
                "currency": "EUR",
                **bounds,
                "elements": [{"restrictions": None, "price_components": [component]}],
            }
        )
        assert tariff.price_per_kwh is None

    tariff = _parse_tariff(
        {
            "currency": "eur",
            "tariff_alt_text": [{"language": "en", "text": "Public tariff"}],
            "elements": [],
        }
    )
    assert tariff.currency == "EUR"
    assert tariff.description == "Public tariff"


def test_tariff_parser_uses_active_date_restricted_energy_price():
    now = datetime(2026, 1, 15, 12, tzinfo=UTC)
    tariff = _parse_tariff(
        {
            "id": "tariff-active-date",
            "type": "AD_HOC_PAYMENT",
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": {
                        "start_time": None,
                        "end_time": None,
                        "start_date": "2025-11-19",
                        "end_date": None,
                        "min_kwh": None,
                        "max_kwh": None,
                        "min_current": None,
                        "max_current": None,
                        "min_power": None,
                        "max_power": None,
                        "min_duration": None,
                        "max_duration": None,
                        "day_of_week": None,
                        "reservation": None,
                    },
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": 0.35,
                            "vat": 21.0,
                            "step_size": 1,
                        }
                    ],
                }
            ],
        },
        now=now,
    )

    assert tariff.price_per_kwh == Decimal("0.4235")
    assert tariff.tariff_id == "tariff-active-date"
    assert tariff.tariff_type == "AD_HOC_PAYMENT"
    assert tariff.status is TariffStatus.AVAILABLE


@pytest.mark.parametrize(
    ("restrictions", "expected_status"),
    [
        ({"start_date": "2026-01-16"}, TariffStatus.INACTIVE),
        ({"end_date": "2026-01-15"}, TariffStatus.INACTIVE),
        ({"start_date": "2025-11-19", "max_kwh": 30}, TariffStatus.CONDITIONAL),
    ],
)
def test_tariff_parser_preserves_unavailable_restriction_reasons(
    restrictions, expected_status
):
    tariff = _parse_tariff(
        {
            "currency": "EUR",
            "elements": [
                {
                    "restrictions": restrictions,
                    "price_components": [
                        {
                            "type": "ENERGY",
                            "price": 0.35,
                            "vat": 21.0,
                            "step_size": 1,
                        }
                    ],
                }
            ],
        },
        now=datetime(2026, 1, 15, 12, tzinfo=UTC),
    )

    assert tariff.price_per_kwh is None
    assert tariff.status is expected_status


@pytest.mark.parametrize("invalid_date", [123, "not-a-date", "20260115"])
def test_tariff_parser_rejects_invalid_restriction_dates(invalid_date):
    with pytest.raises(InvalidProviderResponseError):
        _parse_tariff(
            {
                "currency": "EUR",
                "elements": [
                    {
                        "restrictions": {"start_date": invalid_date},
                        "price_components": [],
                    }
                ],
            },
            now=datetime(2026, 1, 15, 12, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_tariff_cache_avoids_refetch_and_preserves_unknown_prices(caplog):
    cached = ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW")
    provider = NdwDotNlProvider(FakeSession([]))
    provider._tariff_cache = {"cached": cached}
    provider._cached_tariff_ids = {"cached"}
    provider._tariff_cache_updated = datetime.now(UTC)

    assert await provider._async_get_tariffs({"cached"}) == {"cached": cached}
    assert provider._session.requests == []

    missing_provider = NdwDotNlProvider(
        FakeSession([FakeResponse(gzip.compress(b"[]"), raw=True)])
    )
    with caplog.at_level(logging.WARNING):
        assert await missing_provider._async_get_tariffs({"missing"}) == {
            "missing": None
        }
    assert "prices are unknown" in caplog.text


@pytest.mark.asyncio
async def test_tariff_feed_rejects_invalid_gzip_json_records_and_duplicates():
    duplicate = {"id": "wanted", "currency": "EUR", "elements": []}
    bodies = (
        b"not gzip",
        gzip.compress(b"{"),
        gzip.compress(b"{}"),
        gzip.compress(b"[null]"),
        gzip.compress(json.dumps([duplicate, duplicate]).encode()),
    )
    for body in bodies:
        provider = NdwDotNlProvider(FakeSession([FakeResponse(body, raw=True)]))
        with pytest.raises(InvalidProviderResponseError):
            await provider._async_get_tariffs({"wanted"})


def test_with_station_tariff_attaches_only_a_complete_matching_price(sample_stations):
    station = sample_stations[0]
    missing = _with_station_tariff(station, {})
    assert missing.tariff is None
    assert missing.tariff_status is TariffStatus.MISSING
    assert any(
        connector.tariff_ids and connector.tariff_status is TariffStatus.MISSING
        for connector in missing.connectors
    )
    assert any(
        not connector.tariff_ids
        and connector.tariff_status is TariffStatus.NOT_REPORTED
        for connector in missing.connectors
    )
    assert (
        _with_station_tariff(replace(station, connectors=()), {}).tariff_status
        is TariffStatus.NOT_REPORTED
    )

    connector = replace(station.connectors[0], tariff_ids=("known",))
    station = replace(station, connectors=(connector,))
    tariff = ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW")
    resolved = _with_station_tariff(station, {"known": tariff})
    assert resolved.tariff == tariff
    assert resolved.tariff_status is TariffStatus.AVAILABLE
    assert resolved.connectors[0].tariffs == (tariff,)

    unresolved = _with_station_tariff(station, {"known": None})
    assert unresolved.tariff is None
    assert unresolved.tariff_status is TariffStatus.MISSING


def test_station_tariff_remains_unknown_when_connector_groups_differ(sample_stations):
    station = sample_stations[0]
    first = replace(station.connectors[0], tariff_ids=("first",))
    second = replace(station.connectors[1], tariff_ids=("second",))
    station = replace(station, connectors=(first, second))
    tariffs = {
        "first": ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW"),
        "second": ChargingTariff("EUR", Decimal("0.3"), None, None, None, "NDW"),
    }

    resolved = _with_station_tariff(station, tariffs)

    assert resolved.tariff is None
    assert resolved.tariff_status is TariffStatus.AMBIGUOUS
    assert [
        connector.tariffs[0].price_per_kwh for connector in resolved.connectors
    ] == [Decimal("0.2"), Decimal("0.3")]


@pytest.mark.parametrize(
    "status",
    [TariffStatus.CONDITIONAL, TariffStatus.INACTIVE, TariffStatus.INCOMPLETE],
)
def test_connector_tariff_status_preserves_unresolved_reason(sample_stations, status):
    station = sample_stations[0]
    connector = replace(station.connectors[0], tariff_ids=("restricted",))
    station = replace(station, connectors=(connector,))
    tariff = ChargingTariff("EUR", None, None, None, None, "NDW DOT-NL", status=status)

    resolved = _with_station_tariff(station, {"restricted": tariff})

    assert resolved.tariff is None
    assert resolved.tariff_status is status
    assert resolved.connectors[0].tariff_status is status


def test_connector_with_conflicting_tariffs_is_ambiguous(sample_stations):
    station = sample_stations[0]
    connector = replace(station.connectors[0], tariff_ids=("first", "second"))
    station = replace(station, connectors=(connector,))
    tariffs = {
        "first": ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW"),
        "second": ChargingTariff("EUR", Decimal("0.3"), None, None, None, "NDW"),
    }

    resolved = _with_station_tariff(station, tariffs)

    assert resolved.tariff is None
    assert resolved.tariff_status is TariffStatus.AMBIGUOUS
    assert resolved.connectors[0].tariff_status is TariffStatus.AMBIGUOUS


def test_provider_factory_rejects_unimplemented_providers():
    with pytest.raises(UnsupportedProviderError):
        api.create_provider("unknown", FakeSession([]))


def test_provider_factory_creates_belgium_with_home_assistant_limiter(hass):
    provider = api.create_provider(
        PROVIDER_ECO_MOVEMENT_BE,
        FakeSession([]),
        hass,
        "test-token",
    )

    assert isinstance(provider, BelgiumChargingProvider)
    assert provider.provider_id == PROVIDER_ECO_MOVEMENT_BE
    assert provider.country_code == "BE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse({}, status=403), AuthenticationError),
        (FakeResponse({}, status=500), ProviderConnectionError),
        (TimeoutError("private url"), ProviderConnectionError),
    ],
)
async def test_request_errors_are_translated_without_leaking_urls(response, error):
    provider = NdwDotNlProvider(FakeSession([response]))

    with pytest.raises(error) as raised:
        await provider.async_validate_connection(52.0, 5.0, 500)

    assert "private url" not in str(raised.value)
