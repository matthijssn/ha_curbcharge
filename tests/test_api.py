"""Tests for the NDW DOT-NL API client."""

from __future__ import annotations

import copy
import gzip
import json
import logging
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
)
from custom_components.curbcharge.models import ChargingTariff, ConnectorStatus
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

    stations = await provider.async_get_stations(52.0, 5.0, 100, 2)

    assert [station.provider_id for station in stations] == [
        "NL-TEST-001",
        "NL-TEST-002",
    ]
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
async def test_valid_empty_response_is_empty(sample_geojson):
    session = FakeSession([FakeResponse({"type": "FeatureCollection", "features": []})])
    provider = NdwDotNlProvider(session)

    assert await provider.async_get_stations(52.0, 5.0, 500, 10) == ()


def test_ndw_provider_exposes_stable_country_identity():
    provider = NdwDotNlProvider(FakeSession([]))

    assert provider.provider_id == "ndw_dot_nl"
    assert provider.country_code == "NL"


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
async def test_new_tariff_lookup_refreshes_existing_cached_tariffs():
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
        (("properties", "last_updated"), None),
        (("properties", "last_updated"), "not-a-timestamp"),
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
    assert _with_station_tariff(station, {}).tariff is None
    assert _with_station_tariff(replace(station, connectors=()), {}).tariff is None

    connector = replace(station.connectors[0], tariff_ids=("known",))
    station = replace(station, connectors=(connector,))
    tariff = ChargingTariff("EUR", Decimal("0.2"), None, None, None, "NDW")
    assert _with_station_tariff(station, {"known": tariff}).tariff == tariff
    assert _with_station_tariff(station, {"known": None}).tariff is None


def test_provider_factory_rejects_unimplemented_providers():
    with pytest.raises(UnsupportedProviderError):
        api.create_provider("unknown", FakeSession([]))


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
