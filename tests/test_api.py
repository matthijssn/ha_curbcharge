"""Tests for the NDW DOT-NL API client."""

from __future__ import annotations

import copy
import gzip
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import aiohttp
import pytest

from custom_components.curbcharge.api import (
    NdwDotNlProvider,
    _parse_tariff,
    _with_station_tariff,
)
from custom_components.curbcharge.models import ChargingTariff, ConnectorStatus
from custom_components.curbcharge.provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
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
    assert len(session.requests) == 1


@pytest.mark.asyncio
async def test_valid_empty_response_is_empty(sample_geojson):
    session = FakeSession([FakeResponse({"type": "FeatureCollection", "features": []})])
    provider = NdwDotNlProvider(session)

    assert await provider.async_get_stations(52.0, 5.0, 500, 10) == ()


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
