"""Tests for the Switzerland (SFOE ich-tanke-strom OCPI) provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

import custom_components.curbcharge.providers.switzerland as switzerland
from custom_components.curbcharge.const import (
    COUNTRY_CH,
    PROVIDER_ICH_TANKE_STROM_CH,
)
from custom_components.curbcharge.models import (
    ChargingStation,
    ChargingTariff,
    ConnectorStatus,
    ConnectorTypeId,
    EVSEStatus,
    FavouriteStationReference,
    ProviderTimestampSource,
    TariffStatus,
)
from custom_components.curbcharge.providers import create_provider
from custom_components.curbcharge.providers.base import (
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
)

BASE = switzerland._API_BASE_URL
BERN = (46.9481, 7.4474)


class FakeResponse:
    """Minimal response supporting aiohttp's async context-manager interface."""

    def __init__(
        self,
        payload: object | bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self._body = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload).encode("utf-8")
        )

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._body


class FakeSession:
    """Capture requests and return queued fake responses in order."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str] | None, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None,
        headers: dict[str, str],
        timeout: object,
    ) -> FakeResponse:
        self.requests.append((url, dict(params) if params else None, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _connector(
    *,
    standard: str | None = "IEC_62196_T2",
    connector_format: str | None = "SOCKET",
    power_type: str | None = "AC_3_PHASE",
    max_electric_power: float | None = 22000,
    tariff_ids: list[str] | None = ("tariff-1",),
) -> dict[str, Any]:
    return {
        "id": "connector-1",
        "standard": standard,
        "format": connector_format,
        "power_type": power_type,
        "max_voltage": 400,
        "max_amperage": 32,
        "max_electric_power": max_electric_power,
        "tariff_ids": list(tariff_ids) if tariff_ids is not None else None,
        "last_updated": "2026-10-30T09:00:00Z",
    }


def _evse(
    *,
    uid: str = "uid-1",
    evse_id: str | None = "CH*TST*E0001",
    physical_reference: str | None = None,
    connectors: list[dict[str, Any]] | None = None,
    last_updated: str | None = "2026-10-30T09:00:00Z",
    status: str = "AVAILABLE",
) -> dict[str, Any]:
    return {
        "uid": uid,
        "evse_id": evse_id,
        # Embedded status is stale upstream and must be ignored by the provider.
        "status": status,
        "physical_reference": physical_reference,
        "connectors": connectors if connectors is not None else [_connector()],
        "last_updated": last_updated,
    }


def _location(
    *,
    country_code: str = "CH",
    party_id: str = "TST",
    location_id: str = "LOC1",
    country: str | None = "CHE",
    name: str | None = "Synthetic Station",
    latitude: float = BERN[0],
    longitude: float = BERN[1],
    operator_name: str | None = "Synthetic Operator",
    evses: list[dict[str, Any]] | None = None,
    last_updated: str | None = "2026-10-30T09:00:00Z",
    address: str | None = "Bahnhofplatz 1",
    postal_code: str | None = "3011",
    city: str | None = "Bern",
) -> dict[str, Any]:
    return {
        "country_code": country_code,
        "party_id": party_id,
        "id": location_id,
        "publish": True,
        "name": name,
        "address": address,
        "city": city,
        "postal_code": postal_code,
        "country": country,
        "coordinates": {"latitude": str(latitude), "longitude": str(longitude)},
        "evses": evses if evses is not None else [_evse()],
        "operator": {"name": operator_name} if operator_name is not None else None,
        "time_zone": "Europe/Zurich",
        "last_updated": last_updated,
    }


def _status(
    *,
    country_code: str = "CH",
    party_id: str = "TST",
    location_id: str = "LOC1",
    evse_uid: str = "uid-1",
    status: str = "AVAILABLE",
    last_updated: str | None = "2026-11-03T09:00:00Z",
) -> dict[str, Any]:
    return {
        "country_code": country_code,
        "party_id": party_id,
        "location_id": location_id,
        "evse_uid": evse_uid,
        "status": status,
        "last_updated": last_updated,
    }


def _tariff(
    *,
    tariff_id: str = "tariff-1",
    currency: str = "CHF",
    tax_included: str = "NO",
    price: float = 0.5,
    vat: float | None = 8.1,
    component_type: str = "ENERGY",
    step_size: float = 1,
    start: str | None = None,
    end: str | None = None,
    restrictions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": component_type,
        "price": price,
        "vat": vat,
        "step_size": step_size,
    }
    element: dict[str, Any] = {"price_components": [component]}
    if restrictions is not None:
        element["restrictions"] = restrictions
    return {
        "country_code": "CH",
        "party_id": "TST",
        "id": tariff_id,
        "currency": currency,
        "tax_included": tax_included,
        "elements": [element],
        "start_date_time": start,
        "end_date_time": end,
        "last_updated": "2026-10-30T09:00:00Z",
    }


def _envelope(data: object, status_code: int = 1000) -> dict[str, Any]:
    return {
        "data": data,
        "status_code": status_code,
        "status_message": "Success",
        "timestamp": "2026-11-03T09:30:00Z",
    }


def _page(items: list[dict[str, Any]], next_url: str | None = None) -> FakeResponse:
    headers = {"X-Limit": "1000"}
    if next_url is not None:
        headers["Link"] = f'<{next_url}>; rel="next"'
    return FakeResponse(_envelope(items), headers=headers)


def _full_payloads(
    *,
    locations: list[dict[str, Any]] | None = None,
    tariffs: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
) -> list[FakeResponse]:
    """Build the three queued responses for one async_get_stations call."""
    return [
        _page(locations if locations is not None else [_location()]),
        _page(tariffs if tariffs is not None else [_tariff()]),
        _page(statuses if statuses is not None else [_status()]),
    ]


def _provider(session: FakeSession) -> switzerland.SwitzerlandChargingProvider:
    provider = create_provider(PROVIDER_ICH_TANKE_STROM_CH, session)
    assert isinstance(provider, switzerland.SwitzerlandChargingProvider)
    return provider


@pytest.mark.asyncio
async def test_switzerland_maps_location_evse_connector_status_and_tariff():
    session = FakeSession(_full_payloads())
    provider = _provider(session)

    assert provider.provider_id == PROVIDER_ICH_TANKE_STROM_CH
    assert provider.provider_name == "ich-tanke-strom Switzerland"
    assert provider.country_code == COUNTRY_CH

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert [station.provider_id for station in result.nearby] == ["CH*TST*LOC1*uid-1"]
    station = result.nearby[0]
    assert station.name == "Synthetic Station"
    assert station.evse_id == "CH*TST*E0001"
    assert station.country_code == COUNTRY_CH
    assert station.provider == PROVIDER_ICH_TANKE_STROM_CH
    assert station.operator == "Synthetic Operator"
    assert station.operator_id == "CH*TST"
    assert station.address == "Bahnhofplatz 1, 3011 Bern"
    assert station.distance_m == 0
    assert station.evse_status is EVSEStatus.AVAILABLE
    assert station.availability_reported is True
    assert station.last_updated == datetime(2026, 11, 3, 9, 0, 0, tzinfo=UTC)
    assert (
        station.last_updated_source is ProviderTimestampSource.SWITZERLAND_EVSE_STATUS
    )
    assert station.has_available_connectors is True
    assert station.location is not None
    assert station.location.provider_id == "CH*TST*LOC1"

    connector = station.connectors[0]
    assert connector.connector_type == "IEC_62196_T2"
    assert connector.connector_type_id is ConnectorTypeId.TYPE_2
    assert connector.status is ConnectorStatus.UNKNOWN
    assert connector.max_power_kw == 22
    assert connector.connector_format == "SOCKET"
    assert connector.power_type == "AC_3_PHASE"

    assert station.tariff is not None
    assert station.tariff.currency == "CHF"
    assert station.tariff.price_per_kwh == Decimal("0.5") * Decimal("1.081")
    assert station.tariff.vat_percentage == Decimal("8.1")
    assert station.tariff_status is TariffStatus.AVAILABLE

    # Requests go: locations page, tariffs page, initial status sync.
    assert len(session.requests) == 3
    for _url, _params, headers in session.requests:
        assert headers["Accept"] == "application/json"
        assert headers["User-Agent"] == "ha_curbcharge"
    assert session.requests[0][0] == f"{BASE}/locations"
    assert session.requests[0][1] == {"limit": "1000"}
    assert session.requests[1][0] == f"{BASE}/tariffs"
    assert session.requests[2][0] == f"{BASE}/status"
    assert session.requests[2][1] == {
        "date_from": "2000-01-01T00:00:00Z",
        "limit": "5000",
    }


@pytest.mark.asyncio
async def test_switzerland_keeps_foreign_operator_at_swiss_location():
    """A FR operator serving a CHE site is kept; the filter is on location country."""
    location = _location(country_code="FR", party_id="073", country="CHE")
    session = FakeSession(
        _full_payloads(
            locations=[location],
            tariffs=[],
            statuses=[_status(country_code="FR", party_id="073")],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert [station.provider_id for station in result.nearby] == ["FR*073*LOC1*uid-1"]
    assert result.nearby[0].country_code == COUNTRY_CH
    assert result.nearby[0].operator_id == "FR*073"
    assert result.nearby[0].evse_status is EVSEStatus.AVAILABLE


@pytest.mark.asyncio
async def test_switzerland_drops_locations_outside_switzerland():
    session = FakeSession(
        _full_payloads(
            locations=[_location(country="LIE"), _location(country=None)],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_switzerland_ignores_embedded_status_without_delta_record():
    """The catalogue's evse.status is stale upstream and must not be used."""
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[_evse(status="CHARGING")])],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10, include_unknown=True)

    station = result.nearby[0]
    assert station.availability_reported is False
    assert station.evse_status is EVSEStatus.UNKNOWN
    assert station.has_available_connectors is None
    assert station.last_updated == datetime(2026, 10, 30, 9, 0, 0, tzinfo=UTC)
    assert (
        station.last_updated_source
        is ProviderTimestampSource.SWITZERLAND_STATIC_FALLBACK
    )
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.MISSING


@pytest.mark.asyncio
async def test_switzerland_status_without_timestamp_or_evse_timestamp_has_no_source():
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[_evse(last_updated=None)])],
            tariffs=[],
            statuses=[_status(last_updated=None)],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    station = result.nearby[0]
    assert station.evse_status is EVSEStatus.AVAILABLE
    assert station.availability_reported is False
    assert station.last_updated is None
    assert station.last_updated_source is None


@pytest.mark.asyncio
async def test_switzerland_delta_poll_merges_newer_status_and_keeps_others():
    evses = [_evse(uid="uid-1"), _evse(uid="uid-2", evse_id="CH*TST*E0002")]
    session = FakeSession(
        [
            *_full_payloads(
                locations=[_location(evses=evses)],
                tariffs=[],
                statuses=[
                    _status(evse_uid="uid-1", status="AVAILABLE"),
                    _status(evse_uid="uid-2", status="AVAILABLE"),
                ],
            ),
            _page(
                [
                    _status(
                        evse_uid="uid-1",
                        status="CHARGING",
                        last_updated="2026-11-03T09:10:00Z",
                    )
                ]
            ),
        ]
    )
    provider = _provider(session)

    first = await provider.async_get_stations(*BERN, 500, 10)
    second = await provider.async_get_stations(*BERN, 500, 10)

    assert {s.provider_id: s.evse_status for s in first.nearby} == {
        "CH*TST*LOC1*uid-1": EVSEStatus.AVAILABLE,
        "CH*TST*LOC1*uid-2": EVSEStatus.AVAILABLE,
    }
    assert {s.provider_id: s.evse_status for s in second.nearby} == {
        "CH*TST*LOC1*uid-1": EVSEStatus.CHARGING,
        "CH*TST*LOC1*uid-2": EVSEStatus.AVAILABLE,
    }
    # Second poll only re-fetches statuses with a bounded date_from overlap.
    assert len(session.requests) == 4
    delta_params = session.requests[3][1]
    assert delta_params is not None
    assert delta_params["limit"] == "5000"
    date_from = datetime.fromisoformat(delta_params["date_from"].replace("Z", "+00:00"))
    assert date_from.tzinfo is not None
    assert date_from < datetime.now(UTC)
    assert date_from > datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_switzerland_delta_poll_ignores_older_status_record():
    session = FakeSession(
        [
            *_full_payloads(
                tariffs=[],
                statuses=[
                    _status(status="CHARGING", last_updated="2026-11-03T09:10:00Z")
                ],
            ),
            _page([_status(status="AVAILABLE", last_updated="2026-11-03T09:00:00Z")]),
        ]
    )
    provider = _provider(session)

    await provider.async_get_stations(*BERN, 500, 10)
    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].evse_status is EVSEStatus.CHARGING


@pytest.mark.asyncio
async def test_switzerland_physical_reference_disambiguates_evse_name():
    evses = [
        _evse(uid="uid-1", physical_reference="A1"),
        _evse(uid="uid-2", evse_id="CH*TST*E0002", physical_reference="A2"),
    ]
    session = FakeSession(
        _full_payloads(locations=[_location(evses=evses)], tariffs=[], statuses=[])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    names = {station.provider_id: station.name for station in result.nearby}
    assert names == {
        "CH*TST*LOC1*uid-1": "Synthetic Station (A1)",
        "CH*TST*LOC1*uid-2": "Synthetic Station (A2)",
    }


@pytest.mark.asyncio
async def test_switzerland_falls_back_to_uid_and_location_key_for_names():
    location = _location(
        name=None, address=None, postal_code=None, city=None, operator_name=None
    )
    location["evses"] = [_evse(evse_id=None)]
    session = FakeSession(_full_payloads(locations=[location], tariffs=[], statuses=[]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    station = result.nearby[0]
    assert station.name == "CH*TST*LOC1"
    assert station.evse_id == "uid-1"
    assert station.address is None
    assert station.operator is None


@pytest.mark.asyncio
async def test_switzerland_normalises_shifted_address_whitespace():
    """Live records carry tabs and shifted fields; text is cleaned, not trusted."""
    location = _location(
        address="Luegetenstrasse 5\t", postal_code="Menzingen", city="6313\t"
    )
    session = FakeSession(_full_payloads(locations=[location], tariffs=[], statuses=[]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].address == "Luegetenstrasse 5, Menzingen 6313"


@pytest.mark.asyncio
async def test_switzerland_excludes_removed_evse():
    session = FakeSession(
        _full_payloads(tariffs=[], statuses=[_status(status="REMOVED")])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_switzerland_filters_by_radius_and_sorts_by_distance():
    near = _location(
        location_id="NEAR",
        latitude=BERN[0] + 0.0001,
        evses=[_evse(uid="near", connectors=[_connector(tariff_ids=[])])],
    )
    far = _location(
        location_id="FAR",
        latitude=47.37,
        longitude=8.54,
        evses=[_evse(uid="far", connectors=[_connector(tariff_ids=[])])],
    )
    session = FakeSession(
        _full_payloads(
            locations=[far, near],
            tariffs=[],
            statuses=[
                _status(location_id="NEAR", evse_uid="near"),
                _status(location_id="FAR", evse_uid="far"),
            ],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 1000, 10)

    assert [station.provider_id for station in result.nearby] == ["CH*TST*NEAR*near"]


@pytest.mark.asyncio
async def test_switzerland_respects_max_stations_limit():
    evses = [
        _evse(uid=f"uid-{i}", connectors=[_connector(tariff_ids=[])]) for i in range(3)
    ]
    statuses = [_status(evse_uid=f"uid-{i}") for i in range(3)]
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=evses)], tariffs=[], statuses=statuses
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 2)

    assert len(result.nearby) == 2


@pytest.mark.asyncio
async def test_switzerland_includes_favourite_outside_radius_without_extra_request():
    near = _location(
        location_id="NEAR",
        evses=[_evse(uid="near", connectors=[_connector(tariff_ids=[])])],
    )
    far = _location(
        location_id="FAR",
        latitude=47.37,
        longitude=8.54,
        evses=[_evse(uid="far", connectors=[_connector(tariff_ids=[])])],
    )
    session = FakeSession(
        _full_payloads(
            locations=[near, far],
            tariffs=[],
            statuses=[
                _status(location_id="NEAR", evse_uid="near"),
                _status(location_id="FAR", evse_uid="far"),
            ],
        )
    )
    provider = _provider(session)
    favourite = FavouriteStationReference(
        provider=PROVIDER_ICH_TANKE_STROM_CH,
        provider_id="CH*TST*FAR*far",
        name="Far favourite",
        latitude=47.37,
        longitude=8.54,
    )

    result = await provider.async_get_stations(*BERN, 100, 10, favourites=(favourite,))

    assert [station.provider_id for station in result.nearby] == ["CH*TST*NEAR*near"]
    assert [station.provider_id for station in result.favourites] == ["CH*TST*FAR*far"]
    assert len(session.requests) == 3


@pytest.mark.asyncio
async def test_switzerland_rejects_favourite_from_a_different_provider():
    session = FakeSession(_full_payloads())
    provider = _provider(session)
    favourite = FavouriteStationReference(
        provider="other_provider",
        provider_id="CH*TST*LOC1*uid-1",
        name="Wrong provider",
        latitude=BERN[0],
        longitude=BERN[1],
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(*BERN, 500, 10, favourites=(favourite,))


@pytest.mark.asyncio
async def test_switzerland_include_unknown_false_excludes_unreported_availability():
    session = FakeSession(_full_payloads(tariffs=[], statuses=[]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10, include_unknown=False)

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_switzerland_pagination_follows_link_header_across_pages():
    page_one = _page(
        [
            _location(
                location_id="L1",
                evses=[_evse(uid="e1", connectors=[_connector(tariff_ids=[])])],
            )
        ],
        next_url=f"{BASE}/locations?offset=1000&limit=1000",
    )
    page_two = _page(
        [
            _location(
                location_id="L2",
                evses=[_evse(uid="e2", connectors=[_connector(tariff_ids=[])])],
            )
        ]
    )
    session = FakeSession([page_one, page_two, _page([]), _page([])])
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10, include_unknown=True)

    assert {station.provider_id for station in result.nearby} == {
        "CH*TST*L1*e1",
        "CH*TST*L2*e2",
    }
    assert len(session.requests) == 4
    assert session.requests[0][1] == {"limit": "1000"}
    assert session.requests[1][0] == f"{BASE}/locations?offset=1000&limit=1000"
    assert session.requests[1][1] is None


@pytest.mark.asyncio
async def test_switzerland_pagination_raises_on_repeating_link():
    loop_url = f"{BASE}/locations?offset=1000&limit=1000"
    session = FakeSession([_page([], next_url=loop_url), _page([], next_url=loop_url)])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="pagination link"):
        await provider.async_get_stations(*BERN, 500, 10)


@pytest.mark.asyncio
async def test_switzerland_pagination_rejects_link_outside_api_origin():
    session = FakeSession(
        [_page([], next_url="https://example.invalid/ocpi/locations?offset=1000")]
    )
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="pagination link"):
        await provider.async_get_stations(*BERN, 500, 10)


@pytest.mark.asyncio
async def test_switzerland_pagination_raises_when_page_bound_exceeded():
    responses = [
        _page([], next_url=f"{BASE}/locations?offset={(i + 1) * 1000}&limit=1000")
        for i in range(60)
    ]
    session = FakeSession(responses)
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="expected page bound"):
        await provider.async_get_stations(*BERN, 500, 10)


@pytest.mark.asyncio
async def test_switzerland_catalog_is_cached_between_refreshes():
    session = FakeSession([*_full_payloads(tariffs=[], statuses=[]), _page([])])
    provider = _provider(session)

    await provider.async_get_stations(*BERN, 500, 10)
    await provider.async_get_stations(*BERN, 500, 10)

    # Second call only re-fetches statuses; locations/tariffs stay cached.
    assert len(session.requests) == 4


@pytest.mark.asyncio
async def test_switzerland_catalog_refetches_when_cache_expired(monkeypatch):
    session = FakeSession(
        [
            *_full_payloads(tariffs=[], statuses=[]),
            *_full_payloads(tariffs=[], statuses=[]),
        ]
    )
    provider = _provider(session)
    await provider.async_get_stations(*BERN, 500, 10)
    monkeypatch.setattr(switzerland, "_CATALOG_CACHE_SECONDS", -1)

    await provider.async_get_stations(*BERN, 500, 10)

    assert len(session.requests) == 6


@pytest.mark.asyncio
async def test_switzerland_rate_limit_defers_and_reraises():
    session = FakeSession([FakeResponse({}, status=429, headers={"Retry-After": "7"})])
    provider = _provider(session)

    with pytest.raises(ProviderRateLimitError) as info:
        await provider.async_validate_connection(*BERN, 500)
    assert info.value.retry_after == 7


@pytest.mark.asyncio
async def test_switzerland_server_error_raises_connection_error():
    session = FakeSession([FakeResponse({}, status=503)])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.asyncio
async def test_switzerland_unexpected_status_raises_connection_error_not_auth():
    session = FakeSession([FakeResponse({}, status=401)])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.asyncio
async def test_switzerland_invalid_json_raises():
    session = FakeSession([FakeResponse(b"{not json")])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="invalid JSON"):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.asyncio
async def test_switzerland_unsuccessful_ocpi_status_code_raises():
    session = FakeSession([FakeResponse(_envelope(None, status_code=2001))])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="OCPI status code"):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.asyncio
async def test_switzerland_malformed_location_raises():
    broken = _location()
    del broken["id"]
    session = FakeSession([_page([broken])])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.asyncio
async def test_switzerland_validate_connection_requests_a_single_location():
    session = FakeSession([_page([_location()])])
    provider = _provider(session)

    await provider.async_validate_connection(*BERN, 500)

    assert session.requests == [
        (
            f"{BASE}/locations",
            {"limit": "1"},
            {"Accept": "application/json", "User-Agent": "ha_curbcharge"},
        )
    ]
    assert provider._catalog is None


@pytest.mark.asyncio
async def test_switzerland_timeout_raises_connection_error():
    session = FakeSession([TimeoutError("synthetic timeout")])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(*BERN, 500)


@pytest.mark.parametrize("radius_m", [99, 10_001])
@pytest.mark.asyncio
async def test_switzerland_rejects_unsupported_search_radius(radius_m):
    provider = _provider(FakeSession([]))

    with pytest.raises(UnsupportedLocationError):
        await provider.async_validate_connection(*BERN, radius_m)


@pytest.mark.asyncio
async def test_switzerland_tariff_time_of_day_restriction_is_conditional():
    session = FakeSession(
        _full_payloads(
            tariffs=[_tariff(restrictions={"start_time": "08:00", "end_time": "20:00"})]
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    station = result.nearby[0]
    assert station.tariff_status is TariffStatus.CONDITIONAL
    assert station.tariff is None


@pytest.mark.asyncio
async def test_switzerland_tariff_inactive_date_window_hides_price():
    session = FakeSession(_full_payloads(tariffs=[_tariff(end="2020-01-01T00:00:00Z")]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    station = result.nearby[0]
    assert station.tariff_status is TariffStatus.INACTIVE
    assert station.tariff is None


@pytest.mark.asyncio
async def test_switzerland_tariff_tax_included_keeps_price():
    session = FakeSession(
        _full_payloads(tariffs=[_tariff(tax_included="YES", price=0.6, vat=8.1)])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff is not None
    assert result.nearby[0].tariff.price_per_kwh == Decimal("0.6")


@pytest.mark.asyncio
async def test_switzerland_tariff_with_time_component_reports_per_minute_price():
    tariff = _tariff()
    tariff["elements"][0]["price_components"].append(
        {"type": "TIME", "price": 6.0, "vat": 8.1, "step_size": 60}
    )
    session = FakeSession(_full_payloads(tariffs=[tariff]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff is not None
    assert result.nearby[0].tariff.price_per_minute == Decimal("6.0") * Decimal("1.081")


@pytest.mark.asyncio
async def test_switzerland_unmapped_connector_standard_keeps_raw_type():
    session = FakeSession(
        _full_payloads(
            locations=[
                _location(
                    evses=[
                        _evse(connectors=[_connector(standard="MCS", tariff_ids=[])])
                    ]
                )
            ],
            tariffs=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    connector = result.nearby[0].connectors[0]
    assert connector.connector_type == "MCS"
    assert connector.connector_type_id is None


def test_switzerland_factory_shares_limiter_via_hass(hass: HomeAssistant):
    session = FakeSession([])
    first = create_provider(PROVIDER_ICH_TANKE_STROM_CH, session, hass)
    second = create_provider(PROVIDER_ICH_TANKE_STROM_CH, session, hass)
    assert isinstance(first, switzerland.SwitzerlandChargingProvider)
    assert isinstance(second, switzerland.SwitzerlandChargingProvider)
    assert first._request_limiter is second._request_limiter


def test_switzerland_factory_rejects_unsupported_provider():
    with pytest.raises(UnsupportedProviderError):
        create_provider("unknown_provider", FakeSession([]))


def test_switzerland_shared_limiter_rejects_invalid_stored_type(hass: HomeAssistant):
    hass.data.setdefault("curbcharge", {})["switzerland_request_limiter"] = object()
    with pytest.raises(RuntimeError):
        switzerland._shared_request_limiter(hass)


@pytest.mark.asyncio
async def test_switzerland_limiter_waits_for_retry_after():
    limiter = switzerland.SwitzerlandRequestLimiter()
    limiter.defer_for_retry_after(None)
    limiter.defer_for_retry_after(0)
    await limiter.async_wait()
    limiter.defer_for_retry_after(1)
    assert limiter._retry_after_until > 0


@pytest.mark.asyncio
async def test_switzerland_rejects_non_list_tariff_ids():
    bad_connector = {**_connector(), "tariff_ids": "not-a-list"}
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[_evse(connectors=[bad_connector])])],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(*BERN, 500, 10)


@pytest.mark.asyncio
async def test_switzerland_evse_without_connectors_reports_not_reported_tariff():
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[_evse(connectors=[])])],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.NOT_REPORTED


@pytest.mark.asyncio
async def test_switzerland_station_tariff_ambiguous_when_connectors_disagree():
    evse = _evse(
        connectors=[_connector(tariff_ids=["t1"]), _connector(tariff_ids=["t2"])]
    )
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[evse])],
            tariffs=[
                _tariff(tariff_id="t1", price=0.5),
                _tariff(tariff_id="t2", price=0.6),
            ],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.AMBIGUOUS
    assert result.nearby[0].tariff is None


@pytest.mark.asyncio
async def test_switzerland_station_tariff_incomplete_when_mixing_reported():
    evse = _evse(connectors=[_connector(tariff_ids=["t1"]), _connector(tariff_ids=[])])
    session = FakeSession(
        _full_payloads(
            locations=[_location(evses=[evse])],
            tariffs=[_tariff(tariff_id="t1")],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.INCOMPLETE


@pytest.mark.asyncio
async def test_switzerland_tariff_element_date_restriction_marks_tariff_inactive():
    future_start = (
        datetime.now(UTC).date().replace(year=datetime.now(UTC).year + 1).isoformat()
    )
    session = FakeSession(
        _full_payloads(tariffs=[_tariff(restrictions={"start_date": future_start})])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.INACTIVE
    assert result.nearby[0].tariff is None


@pytest.mark.asyncio
async def test_switzerland_tariff_mixes_conditional_and_unconditional_elements():
    tariff_record = _tariff()
    tariff_record["elements"].append(
        {
            "price_components": [{"type": "ENERGY", "price": 1.0, "vat": None}],
            "restrictions": {"min_kwh": 1},
        }
    )
    session = FakeSession(_full_payloads(tariffs=[tariff_record]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.CONDITIONAL
    assert result.nearby[0].tariff is None


@pytest.mark.asyncio
async def test_switzerland_tariff_without_elements_is_incomplete():
    tariff_record = _tariff()
    tariff_record["elements"] = []
    session = FakeSession(_full_payloads(tariffs=[tariff_record]))
    provider = _provider(session)

    result = await provider.async_get_stations(*BERN, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.INCOMPLETE


def test_with_station_tariff_without_connectors_is_not_reported():
    station = ChargingStation(
        provider_id="x",
        name="x",
        latitude=0.0,
        longitude=0.0,
        address=None,
        operator=None,
        distance_m=0,
        connectors=(),
        tariff=None,
        last_updated=None,
    )
    assert switzerland._with_station_tariff(station).tariff_status is (
        TariffStatus.NOT_REPORTED
    )


def _tariff_model(status: TariffStatus, price: str | None) -> ChargingTariff:
    return ChargingTariff(
        currency="CHF",
        price_per_kwh=Decimal(price) if price is not None else None,
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=None,
        status=status,
    )


def test_connector_tariff_status_prioritizes_conditional_over_available():
    status = switzerland._connector_tariff_status(
        ("a", "b"),
        (
            _tariff_model(TariffStatus.CONDITIONAL, None),
            _tariff_model(TariffStatus.AVAILABLE, "0.5"),
        ),
    )
    assert status is TariffStatus.CONDITIONAL


def test_connector_tariff_status_ambiguous_when_available_tariffs_differ():
    status = switzerland._connector_tariff_status(
        ("a", "b"),
        (
            _tariff_model(TariffStatus.AVAILABLE, "0.5"),
            _tariff_model(TariffStatus.AVAILABLE, "0.6"),
        ),
    )
    assert status is TariffStatus.AMBIGUOUS


def test_tariff_description_extracts_first_text():
    assert switzerland._tariff_description(None) is None
    assert switzerland._tariff_description([{"language": "de"}]) is None
    assert (
        switzerland._tariff_description([{"language": "de", "text": "hallo"}])
        == "hallo"
    )


def test_tariff_element_status_evaluates_date_restrictions():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    assert switzerland._tariff_element_status(None, now) is TariffStatus.AVAILABLE
    assert (
        switzerland._tariff_element_status({"start_date": "2026-07-01"}, now)
        is TariffStatus.INACTIVE
    )
    assert (
        switzerland._tariff_element_status({"end_date": "2026-01-01"}, now)
        is TariffStatus.INACTIVE
    )
    assert (
        switzerland._tariff_element_status(
            {"start_date": "2026-01-01", "end_date": "2026-12-31"}, now
        )
        is TariffStatus.AVAILABLE
    )
    assert (
        switzerland._tariff_element_status({"day_of_week": ["MONDAY"]}, now)
        is TariffStatus.CONDITIONAL
    )


def test_restriction_date_rejects_invalid_value():
    with pytest.raises(InvalidProviderResponseError):
        switzerland._restriction_date("not-a-date", "start date")


def test_tariff_is_active_checks_start_and_end_window():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    assert switzerland._tariff_is_active({}, now) is True
    assert (
        switzerland._tariff_is_active({"start_date_time": "2027-01-01T00:00:00Z"}, now)
        is False
    )
    assert (
        switzerland._tariff_is_active({"end_date_time": "2020-01-01T00:00:00Z"}, now)
        is False
    )


@pytest.mark.parametrize(
    "record",
    [
        {"currency": "CH", "tax_included": "NO", "elements": []},
        {"currency": "CHF", "tax_included": "MAYBE", "elements": []},
        {"currency": "CHF", "tax_included": "NO", "elements": "oops"},
    ],
)
def test_parse_tariff_record_rejects_invalid_records(record):
    with pytest.raises(InvalidProviderResponseError):
        switzerland._parse_tariff_record(record, datetime.now(UTC))


def test_unique_component_price_returns_none_when_ambiguous_or_unresolved():
    ambiguous = [
        {"type": "ENERGY", "price": 0.5, "vat": None},
        {"type": "ENERGY", "price": 0.6, "vat": None},
    ]
    assert switzerland._unique_component_price(ambiguous, "ENERGY", "YES") == (
        None,
        None,
    )
    unresolved = [{"type": "ENERGY", "price": 0.5, "vat": None}]
    assert switzerland._unique_component_price(unresolved, "ENERGY", "NO") == (
        None,
        None,
    )


def test_unique_time_price_handles_step_size_and_unresolved_prices():
    assert switzerland._unique_time_price(
        [{"type": "TIME", "price": 1.2, "vat": None, "step_size": 60}], "YES"
    ) == Decimal("1.2")
    assert (
        switzerland._unique_time_price(
            [{"type": "TIME", "price": 1.2, "vat": None, "step_size": 0}], "YES"
        )
        is None
    )
    assert (
        switzerland._unique_time_price(
            [{"type": "TIME", "price": 1.2, "vat": None, "step_size": 60}], "NO"
        )
        is None
    )


def test_gross_component_price_tax_handling():
    assert switzerland._gross_component_price({"price": 1.0, "vat": 20}, "YES") == (
        Decimal("1.0"),
        Decimal("20"),
    )
    price, _vat = switzerland._gross_component_price({"price": 1.0, "vat": None}, "NO")
    assert price is None


def test_decimal_price_rejects_invalid_values():
    for value in (True, "abc", float("inf")):
        with pytest.raises(InvalidProviderResponseError):
            switzerland._decimal_price(value, "price")
    assert switzerland._decimal_price("1.5", "price") == Decimal("1.5")


def test_evse_status_maps_known_values_and_defaults_to_unknown():
    assert switzerland._evse_status("outoforder") is EVSEStatus.OUT_OF_ORDER
    assert switzerland._evse_status("BLOCKED") is EVSEStatus.BLOCKED
    assert switzerland._evse_status(None) is EVSEStatus.UNKNOWN
    assert switzerland._evse_status("SOMETHING_NEW") is EVSEStatus.UNKNOWN


def test_optional_nonnegative_power_rejects_invalid_values():
    assert switzerland._optional_nonnegative_power(None) is None
    assert switzerland._optional_nonnegative_power(22000) == 22
    for value in (True, -1, float("nan")):
        with pytest.raises(InvalidProviderResponseError):
            switzerland._optional_nonnegative_power(value)


def test_format_address_handles_missing_parts():
    assert switzerland._format_address({}) is None
    assert (
        switzerland._format_address({"address": "Main 1", "city": "Bern"})
        == "Main 1, Bern"
    )
    assert switzerland._format_address({"address": "  \t "}) is None


def test_coordinates_rejects_invalid_shapes():
    assert switzerland._coordinates({"latitude": "46.9", "longitude": 7.4}) == (
        46.9,
        7.4,
    )
    for value in (
        {"latitude": "abc", "longitude": "7.4"},
        {"latitude": "200", "longitude": "7.4"},
        {"latitude": True, "longitude": "7.4"},
        {"latitude": "nan", "longitude": "7.4"},
        "not-a-dict",
    ):
        with pytest.raises(InvalidProviderResponseError):
            switzerland._coordinates(value)


def test_next_link_parses_rel_next_only():
    assert switzerland._next_link(None) is None
    assert switzerland._next_link('<https://x/y>; rel="prev"') is None
    assert switzerland._next_link('<https://x/y?offset=1>; rel="next"') == (
        "https://x/y?offset=1"
    )


def test_format_datetime_uses_utc_seconds():
    value = datetime(2026, 11, 3, 10, 5, 7, 123456, tzinfo=UTC)
    assert switzerland._format_datetime(value) == "2026-11-03T10:05:07Z"


def test_parse_datetime_rejects_invalid_and_naive_values():
    with pytest.raises(InvalidProviderResponseError):
        switzerland._parse_datetime("not-a-timestamp")
    with pytest.raises(InvalidProviderResponseError, match="timezone-naive"):
        switzerland._parse_datetime("2026-01-01T00:00:00")


def test_optional_datetime_rejects_non_string():
    assert switzerland._optional_datetime(None, "field") is None
    with pytest.raises(InvalidProviderResponseError):
        switzerland._optional_datetime(123, "field")


def test_list_field_rejects_non_list():
    assert switzerland._list_field({}, "data") == []
    with pytest.raises(InvalidProviderResponseError):
        switzerland._list_field({"data": "not-a-list"}, "data")


def test_required_and_optional_string_validation():
    with pytest.raises(InvalidProviderResponseError):
        switzerland._required_string("  ", "field")
    with pytest.raises(InvalidProviderResponseError):
        switzerland._required_string(123, "field")
    assert switzerland._optional_string(None, "field") is None
    assert switzerland._optional_string("  ", "field") is None
    with pytest.raises(InvalidProviderResponseError):
        switzerland._optional_string(123, "field")


def test_string_mapping_rejects_non_dict_and_non_string_keys():
    with pytest.raises(InvalidProviderResponseError):
        switzerland._string_mapping("not-a-dict", "field")
    with pytest.raises(InvalidProviderResponseError):
        switzerland._string_mapping({1: "a"}, "field")


def test_validate_search_rejects_invalid_coordinates_and_limit():
    with pytest.raises(UnsupportedLocationError):
        switzerland._validate_search(float("nan"), 7.0, 500, 10)
    with pytest.raises(UnsupportedLocationError, match="station limit"):
        switzerland._validate_search(46.0, 7.0, 500, 0)
