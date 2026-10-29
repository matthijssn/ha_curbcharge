"""Tests for the Finland (Fintraffic/Digitraffic AFIR) provider."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

import custom_components.curbcharge.finland as finland
from custom_components.curbcharge.api import create_provider
from custom_components.curbcharge.const import (
    COUNTRY_FI,
    PROVIDER_DIGITRAFFIC_AFIR_FI,
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
from custom_components.curbcharge.provider import (
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
)


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
        self.requests: list[tuple[str, dict[str, str], dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        timeout: object,
    ) -> FakeResponse:
        self.requests.append((url, dict(params), dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _connector(
    *,
    standard: str | None = "IEC_62196_T2",
    connector_format: str | None = "CABLE",
    power_type: str | None = "AC_3_PHASE",
    max_electric_power: float | None = 11000,
    tariff_ids: list[str] | None = ("tariff-1",),
) -> dict[str, Any]:
    return {
        "standard": standard,
        "format": connector_format,
        "powerType": power_type,
        "maxVoltage": 400,
        "maxAmperage": 16,
        "maxElectricPower": max_electric_power,
        "tariffIds": list(tariff_ids) if tariff_ids is not None else None,
        "createdAt": "2025-01-01T00:00:00.000Z",
        "modifiedAt": "2026-10-20T10:00:00.000Z",
    }


def _evse(
    *,
    evse_id: str = "FI*TST*E1",
    physical_reference: str | None = None,
    connectors: list[dict[str, Any]] | None = None,
    modified_at: str | None = "2026-10-20T10:00:00.000Z",
) -> dict[str, Any]:
    return {
        "id": evse_id,
        "physicalReference": physical_reference,
        "createdAt": "2025-01-01T00:00:00.000Z",
        "modifiedAt": modified_at,
        "connectors": connectors if connectors is not None else [_connector()],
    }


def _location_feature(
    *,
    location_id: str = "FI*TST*LOC1",
    name: str | None = "Synthetic Station",
    latitude: float = 60.1699,
    longitude: float = 24.9384,
    operator_country_code: str = "FI",
    operator_id: str | None = "FI*TST",
    operator_name: str | None = "Synthetic Operator",
    evses: list[dict[str, Any]] | None = None,
    modified_at: str | None = "2026-10-20T10:00:00.000Z",
) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": {
            "id": location_id,
            "name": name,
            "address": {
                "street": "Testikatu 1",
                "postalCode": "00100",
                "city": "Helsinki",
                "countryCode": "FIN",
            },
            "operator": {
                "id": operator_id,
                "partyId": "TST",
                "countryCode": operator_country_code,
                "details": {"name": operator_name},
            },
            "createdAt": "2025-01-01T00:00:00.000Z",
            "modifiedAt": modified_at,
            "evses": evses if evses is not None else [_evse()],
        },
    }


def _status(
    *,
    evse_id: str = "FI*TST*E1",
    status: str = "AVAILABLE",
    last_updated_at: str | None = "2026-10-29T10:00:00Z",
    modified_at: str = "2026-10-20T10:00:00.000Z",
) -> dict[str, Any]:
    return {
        "evseId": evse_id,
        "status": status,
        "lastUpdatedAt": last_updated_at,
        "modifiedAt": modified_at,
    }


def _tariff(
    *,
    tariff_id: str = "tariff-1",
    currency: str = "EUR",
    tax_included: str = "NO",
    price: float = 0.5,
    vat: float | None = 25.5,
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
        "stepSize": step_size,
    }
    element: dict[str, Any] = {"priceComponents": [component]}
    if restrictions is not None:
        element["restrictions"] = restrictions
    return {
        "id": tariff_id,
        "currency": currency,
        "partyId": "TST",
        "countryCode": "FI",
        "taxIncluded": tax_included,
        "lastUpdated": "2026-10-20T10:00:00Z",
        "elements": [element],
        "startDateTime": start,
        "endDateTime": end,
    }


def _page(
    items_key: str, items: list[dict[str, Any]], next_cursor: str | None = None
) -> dict[str, Any]:
    return {
        items_key: items,
        "pagination": {"nextCursor": next_cursor, "limit": 500},
        "modifiedAt": "2026-10-29T10:00:00Z",
    }


def _full_payloads(
    *,
    features: list[dict[str, Any]] | None = None,
    tariffs: list[dict[str, Any]] | None = None,
    statuses: list[dict[str, Any]] | None = None,
) -> list[FakeResponse]:
    """Build the three queued responses for one async_get_stations call."""
    return [
        FakeResponse(
            _page(
                "features", features if features is not None else [_location_feature()]
            )
        ),
        FakeResponse(_page("tariffs", tariffs if tariffs is not None else [_tariff()])),
        FakeResponse(
            _page("statuses", statuses if statuses is not None else [_status()])
        ),
    ]


def _provider(session: FakeSession) -> finland.FinlandChargingProvider:
    provider = create_provider(PROVIDER_DIGITRAFFIC_AFIR_FI, session)
    assert isinstance(provider, finland.FinlandChargingProvider)
    return provider


@pytest.mark.asyncio
async def test_finland_maps_location_evse_connector_status_and_tariff():
    session = FakeSession(_full_payloads())
    provider = _provider(session)

    assert provider.provider_id == PROVIDER_DIGITRAFFIC_AFIR_FI
    assert provider.provider_name == "Digitraffic AFIR Finland"
    assert provider.country_code == COUNTRY_FI

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert [station.provider_id for station in result.nearby] == ["FI*TST*E1"]
    station = result.nearby[0]
    assert station.name == "Synthetic Station"
    assert station.country_code == COUNTRY_FI
    assert station.provider == PROVIDER_DIGITRAFFIC_AFIR_FI
    assert station.operator == "Synthetic Operator"
    assert station.operator_id == "FI*TST"
    assert station.distance_m == 0
    assert station.evse_status is EVSEStatus.AVAILABLE
    assert station.availability_reported is True
    assert station.last_updated == datetime(2026, 10, 29, 10, 0, 0, tzinfo=UTC)
    assert station.last_updated_source is ProviderTimestampSource.FINLAND_EVSE_STATUS
    assert station.has_available_connectors is True

    connector = station.connectors[0]
    assert connector.connector_type == "IEC_62196_T2"
    assert connector.connector_type_id is ConnectorTypeId.TYPE_2
    assert connector.status is ConnectorStatus.UNKNOWN
    assert connector.max_power_kw == 11
    assert connector.connector_format == "CABLE"
    assert connector.power_type == "AC_3_PHASE"

    assert station.tariff is not None
    assert station.tariff.currency == "EUR"
    assert station.tariff.price_per_kwh is not None
    assert station.tariff_status is TariffStatus.AVAILABLE

    # Requests go: locations page, tariffs page, statuses page.
    assert len(session.requests) == 3
    for _url, params, headers in session.requests:
        assert params["operatorCountryCode"] == "FI"
        assert params["limit"] == "500"
        assert headers["Digitraffic-User"] == "ha_curbcharge"
        assert headers["Accept-Encoding"] == "gzip"
    assert session.requests[0][0].endswith("/locations")
    assert session.requests[1][0].endswith("/tariffs")
    assert session.requests[2][0].endswith("/locations/statuses")


@pytest.mark.asyncio
async def test_finland_physical_reference_disambiguates_evse_name():
    evses = [
        _evse(evse_id="FI*TST*E1", physical_reference="A1"),
        _evse(evse_id="FI*TST*E2", physical_reference="A2"),
    ]
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=evses)], tariffs=[], statuses=[]
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    names = {station.provider_id: station.name for station in result.nearby}
    assert names == {
        "FI*TST*E1": "Synthetic Station (A1)",
        "FI*TST*E2": "Synthetic Station (A2)",
    }


@pytest.mark.asyncio
async def test_finland_missing_status_leaves_availability_unknown():
    session = FakeSession(_full_payloads(tariffs=[], statuses=[]))
    provider = _provider(session)

    result = await provider.async_get_stations(
        60.1699, 24.9384, 500, 10, include_unknown=True
    )

    station = result.nearby[0]
    assert station.availability_reported is False
    assert station.evse_status is EVSEStatus.UNKNOWN
    assert station.has_available_connectors is None
    assert station.last_updated == datetime(2026, 10, 20, 10, 0, 0, tzinfo=UTC)
    assert (
        station.last_updated_source is ProviderTimestampSource.FINLAND_STATIC_FALLBACK
    )
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.MISSING


@pytest.mark.asyncio
async def test_finland_excludes_removed_evse():
    session = FakeSession(
        _full_payloads(tariffs=[], statuses=[_status(status="REMOVED")])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_finland_drops_non_finland_operator_defensively():
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(operator_country_code="NO")],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_finland_filters_by_radius_and_sorts_by_distance():
    near = _location_feature(
        location_id="FI*TST*NEAR",
        latitude=60.1700,
        longitude=24.9384,
        evses=[_evse(evse_id="FI*TST*NEAR", connectors=[_connector(tariff_ids=[])])],
    )
    far = _location_feature(
        location_id="FI*TST*FAR",
        latitude=65.0,
        longitude=25.5,
        evses=[_evse(evse_id="FI*TST*FAR", connectors=[_connector(tariff_ids=[])])],
    )
    session = FakeSession(
        _full_payloads(
            features=[far, near],
            tariffs=[],
            statuses=[
                _status(evse_id="FI*TST*NEAR"),
                _status(evse_id="FI*TST*FAR"),
            ],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 1000, 10)

    assert [station.provider_id for station in result.nearby] == ["FI*TST*NEAR"]


@pytest.mark.asyncio
async def test_finland_respects_max_stations_limit():
    evses = [
        _evse(evse_id=f"FI*TST*E{i}", connectors=[_connector(tariff_ids=[])])
        for i in range(3)
    ]
    statuses = [_status(evse_id=f"FI*TST*E{i}") for i in range(3)]
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=evses)], tariffs=[], statuses=statuses
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 2)

    assert len(result.nearby) == 2


@pytest.mark.asyncio
async def test_finland_includes_favourite_outside_radius_without_extra_request():
    near = _location_feature(
        location_id="FI*TST*NEAR",
        evses=[_evse(evse_id="FI*TST*NEAR", connectors=[_connector(tariff_ids=[])])],
    )
    far = _location_feature(
        location_id="FI*TST*FAR",
        latitude=65.0,
        longitude=25.5,
        evses=[_evse(evse_id="FI*TST*FAR", connectors=[_connector(tariff_ids=[])])],
    )
    session = FakeSession(
        _full_payloads(
            features=[near, far],
            tariffs=[],
            statuses=[
                _status(evse_id="FI*TST*NEAR"),
                _status(evse_id="FI*TST*FAR"),
            ],
        )
    )
    provider = _provider(session)
    favourite = FavouriteStationReference(
        provider=PROVIDER_DIGITRAFFIC_AFIR_FI,
        provider_id="FI*TST*FAR",
        name="Far favourite",
        latitude=65.0,
        longitude=25.5,
    )

    result = await provider.async_get_stations(
        60.1699, 24.9384, 100, 10, favourites=(favourite,)
    )

    assert [station.provider_id for station in result.nearby] == ["FI*TST*NEAR"]
    assert [station.provider_id for station in result.favourites] == ["FI*TST*FAR"]
    # No extra request was needed: the whole country was already fetched.
    assert len(session.requests) == 3


@pytest.mark.asyncio
async def test_finland_rejects_favourite_from_a_different_provider():
    session = FakeSession(_full_payloads())
    provider = _provider(session)
    favourite = FavouriteStationReference(
        provider="other_provider",
        provider_id="FI*TST*E1",
        name="Wrong provider",
        latitude=60.1699,
        longitude=24.9384,
    )

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(
            60.1699, 24.9384, 500, 10, favourites=(favourite,)
        )


@pytest.mark.asyncio
async def test_finland_include_unknown_false_excludes_unreported_availability():
    session = FakeSession(_full_payloads(tariffs=[], statuses=[]))
    provider = _provider(session)

    result = await provider.async_get_stations(
        60.1699, 24.9384, 500, 10, include_unknown=False
    )

    assert result.nearby == ()


@pytest.mark.asyncio
async def test_finland_pagination_follows_cursor_across_pages():
    page_one = FakeResponse(
        _page(
            "features",
            [
                _location_feature(
                    location_id="FI*TST*L1",
                    evses=[
                        _evse(
                            evse_id="FI*TST*E1", connectors=[_connector(tariff_ids=[])]
                        )
                    ],
                )
            ],
            "cursor-a",
        )
    )
    page_two = FakeResponse(
        _page(
            "features",
            [
                _location_feature(
                    location_id="FI*TST*L2",
                    evses=[
                        _evse(
                            evse_id="FI*TST*E2", connectors=[_connector(tariff_ids=[])]
                        )
                    ],
                )
            ],
        )
    )
    session = FakeSession(
        [
            page_one,
            page_two,
            FakeResponse(_page("tariffs", [])),
            FakeResponse(_page("statuses", [])),
        ]
    )
    provider = _provider(session)

    result = await provider.async_get_stations(
        60.1699, 24.9384, 500, 10, include_unknown=True
    )

    assert {station.provider_id for station in result.nearby} == {
        "FI*TST*E1",
        "FI*TST*E2",
    }
    assert len(session.requests) == 4
    assert session.requests[0][1].get("cursor") is None
    assert session.requests[1][1]["cursor"] == "cursor-a"


@pytest.mark.asyncio
async def test_finland_pagination_raises_on_repeating_cursor():
    session = FakeSession(
        [
            FakeResponse(_page("features", [], "same-cursor")),
            FakeResponse(_page("features", [], "same-cursor")),
        ]
    )
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="repeating pagination"):
        await provider.async_get_stations(60.1699, 24.9384, 500, 10)


@pytest.mark.asyncio
async def test_finland_catalog_is_cached_between_refreshes():
    session = FakeSession(
        [
            *_full_payloads(tariffs=[], statuses=[]),
            FakeResponse(_page("statuses", [])),
        ]
    )
    provider = _provider(session)

    await provider.async_get_stations(60.1699, 24.9384, 500, 10)
    await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    # Second call only re-fetches statuses; locations/tariffs stay cached.
    assert len(session.requests) == 4


@pytest.mark.asyncio
async def test_finland_rate_limit_defers_and_reraises():
    session = FakeSession([FakeResponse({}, status=429, headers={"Retry-After": "7"})])
    provider = _provider(session)

    with pytest.raises(ProviderRateLimitError):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.asyncio
async def test_finland_server_error_raises_connection_error():
    session = FakeSession([FakeResponse({}, status=503)])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.asyncio
async def test_finland_unexpected_status_raises_connection_error_not_auth():
    session = FakeSession([FakeResponse({}, status=401)])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.asyncio
async def test_finland_invalid_json_raises():
    session = FakeSession([FakeResponse(b"{not json")])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="invalid JSON"):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.asyncio
async def test_finland_malformed_location_raises():
    broken = _location_feature()
    del broken["properties"]["id"]
    session = FakeSession([FakeResponse(_page("features", [broken]))])
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.asyncio
async def test_finland_timeout_raises_connection_error():
    session = FakeSession([TimeoutError("synthetic timeout")])
    provider = _provider(session)

    with pytest.raises(ProviderConnectionError):
        await provider.async_validate_connection(60.1699, 24.9384, 500)


@pytest.mark.parametrize(
    "radius_m",
    [99, 10_001],
)
@pytest.mark.asyncio
async def test_finland_rejects_unsupported_search_radius(radius_m):
    provider = _provider(FakeSession([]))

    with pytest.raises(UnsupportedLocationError):
        await provider.async_validate_connection(60.1699, 24.9384, radius_m)


@pytest.mark.asyncio
async def test_finland_tariff_conditional_restriction_hides_price():
    session = FakeSession(
        _full_payloads(
            tariffs=[_tariff(restrictions={"minKwh": 1})],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    station = result.nearby[0]
    assert station.tariff_status is TariffStatus.CONDITIONAL
    assert station.tariff is None


@pytest.mark.asyncio
async def test_finland_tariff_inactive_date_window_hides_price():
    session = FakeSession(
        _full_payloads(tariffs=[_tariff(end="2020-01-01T00:00:00.000Z")])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    station = result.nearby[0]
    assert station.tariff_status is TariffStatus.INACTIVE
    assert station.tariff is None


@pytest.mark.asyncio
async def test_finland_tariff_tax_excluded_adds_vat():
    session = FakeSession(
        _full_payloads(tariffs=[_tariff(tax_included="NO", price=1.0, vat=20)])
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    station = result.nearby[0]
    assert station.tariff is not None
    assert station.tariff.price_per_kwh == Decimal("1.2")


@pytest.mark.asyncio
async def test_finland_unmapped_connector_standard_keeps_raw_type():
    session = FakeSession(
        _full_payloads(
            features=[
                _location_feature(
                    evses=[
                        _evse(connectors=[_connector(standard="MCS", tariff_ids=[])])
                    ]
                )
            ],
            tariffs=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    connector = result.nearby[0].connectors[0]
    assert connector.connector_type == "MCS"
    assert connector.connector_type_id is None


def test_finland_factory_shares_limiter_via_hass(hass: HomeAssistant):
    session = FakeSession([])
    first = create_provider(PROVIDER_DIGITRAFFIC_AFIR_FI, session, hass)
    second = create_provider(PROVIDER_DIGITRAFFIC_AFIR_FI, session, hass)
    assert isinstance(first, finland.FinlandChargingProvider)
    assert isinstance(second, finland.FinlandChargingProvider)
    assert first._request_limiter is second._request_limiter


def test_finland_factory_rejects_unsupported_provider():
    with pytest.raises(UnsupportedProviderError):
        create_provider("unknown_provider", FakeSession([]))


def test_finland_shared_limiter_rejects_invalid_stored_type(hass: HomeAssistant):
    hass.data.setdefault("curbcharge", {})["finland_request_limiter"] = object()
    with pytest.raises(RuntimeError):
        finland._shared_request_limiter(hass)


@pytest.mark.asyncio
async def test_finland_pagination_raises_when_page_bound_exceeded():
    responses = [FakeResponse(_page("features", [], f"cursor-{i}")) for i in range(60)]
    session = FakeSession(responses)
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError, match="expected page bound"):
        await provider.async_get_stations(60.1699, 24.9384, 500, 10)


@pytest.mark.asyncio
async def test_finland_rejects_non_list_tariff_ids():
    bad_connector = {**_connector(), "tariffIds": "not-a-list"}
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=[_evse(connectors=[bad_connector])])],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    with pytest.raises(InvalidProviderResponseError):
        await provider.async_get_stations(60.1699, 24.9384, 500, 10)


@pytest.mark.asyncio
async def test_finland_evse_without_connectors_reports_not_reported_tariff():
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=[_evse(connectors=[])])],
            tariffs=[],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.NOT_REPORTED


@pytest.mark.asyncio
async def test_finland_station_tariff_ambiguous_when_connectors_disagree():
    evse = _evse(
        connectors=[
            _connector(tariff_ids=["t1"]),
            _connector(tariff_ids=["t2"]),
        ]
    )
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=[evse])],
            tariffs=[
                _tariff(tariff_id="t1", price=0.5),
                _tariff(tariff_id="t2", price=0.6),
            ],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    station = result.nearby[0]
    assert station.tariff_status is TariffStatus.AMBIGUOUS
    assert station.tariff is None


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
    result = finland._with_station_tariff(station)
    assert result.tariff_status is TariffStatus.NOT_REPORTED


def test_connector_tariff_status_prioritizes_conditional_over_available():
    conditional = ChargingTariff(
        currency="EUR",
        price_per_kwh=None,
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=None,
        status=TariffStatus.CONDITIONAL,
    )
    available = ChargingTariff(
        currency="EUR",
        price_per_kwh=Decimal("0.5"),
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=None,
        status=TariffStatus.AVAILABLE,
    )
    status = finland._connector_tariff_status(("a", "b"), (conditional, available))
    assert status is TariffStatus.CONDITIONAL


def test_connector_tariff_status_ambiguous_when_available_tariffs_differ():
    first = ChargingTariff(
        currency="EUR",
        price_per_kwh=Decimal("0.5"),
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=None,
        status=TariffStatus.AVAILABLE,
    )
    second = ChargingTariff(
        currency="EUR",
        price_per_kwh=Decimal("0.6"),
        session_fee=None,
        price_per_minute=None,
        description=None,
        source=None,
        status=TariffStatus.AVAILABLE,
    )
    status = finland._connector_tariff_status(("a", "b"), (first, second))
    assert status is TariffStatus.AMBIGUOUS


def test_tariff_description_extracts_first_text():
    assert finland._tariff_description(None) is None
    assert finland._tariff_description([{"language": "en"}]) is None
    assert finland._tariff_description([{"language": "en", "text": "hello"}]) == "hello"


def test_tariff_element_status_evaluates_date_restrictions():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    assert finland._tariff_element_status(None, now) is TariffStatus.AVAILABLE
    assert (
        finland._tariff_element_status({"startDate": "2026-07-01"}, now)
        is TariffStatus.INACTIVE
    )
    assert (
        finland._tariff_element_status({"endDate": "2026-01-01"}, now)
        is TariffStatus.INACTIVE
    )
    assert (
        finland._tariff_element_status(
            {"startDate": "2026-01-01", "endDate": "2026-12-31"}, now
        )
        is TariffStatus.AVAILABLE
    )


def test_restriction_date_rejects_invalid_value():
    with pytest.raises(InvalidProviderResponseError):
        finland._restriction_date("not-a-date", "start date")


def test_tariff_is_active_checks_start_and_end_window():
    now = datetime(2026, 6, 15, tzinfo=UTC)
    assert finland._tariff_is_active({}, now) is True
    assert (
        finland._tariff_is_active({"startDateTime": "2027-01-01T00:00:00Z"}, now)
        is False
    )
    assert (
        finland._tariff_is_active({"endDateTime": "2020-01-01T00:00:00Z"}, now) is False
    )


def test_parse_tariff_record_rejects_invalid_currency():
    with pytest.raises(InvalidProviderResponseError):
        finland._parse_tariff_record(
            {"currency": "EU", "taxIncluded": "NO", "elements": []},
            datetime.now(UTC),
        )


def test_parse_tariff_record_rejects_invalid_tax_information():
    with pytest.raises(InvalidProviderResponseError):
        finland._parse_tariff_record(
            {"currency": "EUR", "taxIncluded": "MAYBE", "elements": []},
            datetime.now(UTC),
        )


def test_parse_tariff_record_rejects_non_list_elements():
    with pytest.raises(InvalidProviderResponseError):
        finland._parse_tariff_record(
            {"currency": "EUR", "taxIncluded": "NO", "elements": "oops"},
            datetime.now(UTC),
        )


def test_unique_component_price_returns_none_when_ambiguous():
    components = [
        {"type": "ENERGY", "price": 0.5, "vat": None},
        {"type": "ENERGY", "price": 0.6, "vat": None},
    ]
    price, vat = finland._unique_component_price(components, "ENERGY", "YES")
    assert price is None
    assert vat is None


def test_unique_time_price_converts_step_size_to_per_minute():
    components = [{"type": "TIME", "price": 1.2, "vat": None, "stepSize": 60}]
    assert finland._unique_time_price(components, "YES") == Decimal("1.2")


def test_unique_time_price_rejects_invalid_step_size():
    components = [{"type": "TIME", "price": 1.2, "vat": None, "stepSize": 0}]
    assert finland._unique_time_price(components, "YES") is None


def test_gross_component_price_tax_included_keeps_price_as_is():
    price, vat = finland._gross_component_price({"price": 1.0, "vat": 20}, "YES")
    assert price == Decimal("1.0")
    assert vat == Decimal("20")


def test_gross_component_price_without_vat_and_tax_excluded_is_unknown():
    price, _vat = finland._gross_component_price({"price": 1.0, "vat": None}, "NO")
    assert price is None


def test_decimal_price_rejects_invalid_values():
    with pytest.raises(InvalidProviderResponseError):
        finland._decimal_price(True, "price")
    with pytest.raises(InvalidProviderResponseError):
        finland._decimal_price("abc", "price")
    with pytest.raises(InvalidProviderResponseError):
        finland._decimal_price(float("inf"), "price")
    assert finland._decimal_price("1.5", "price") == Decimal("1.5")


def test_evse_status_defaults_to_unknown_for_non_string():
    assert finland._evse_status(None) is EVSEStatus.UNKNOWN
    assert finland._evse_status(123) is EVSEStatus.UNKNOWN


def test_optional_nonnegative_power_rejects_invalid_values():
    assert finland._optional_nonnegative_power(None) is None
    with pytest.raises(InvalidProviderResponseError):
        finland._optional_nonnegative_power(True)
    with pytest.raises(InvalidProviderResponseError):
        finland._optional_nonnegative_power(-1)


def test_format_address_handles_missing_parts():
    assert finland._format_address(None) is None
    assert (
        finland._format_address({"street": None, "postalCode": None, "city": None})
        is None
    )
    assert (
        finland._format_address({"street": "Main 1", "city": "Helsinki"})
        == "Main 1, Helsinki"
    )


def test_geometry_coordinates_rejects_invalid_shapes():
    with pytest.raises(InvalidProviderResponseError):
        finland._geometry_coordinates({"coordinates": [1]}, "geometry")
    with pytest.raises(InvalidProviderResponseError):
        finland._geometry_coordinates({"coordinates": [200, 10]}, "geometry")


def test_parse_datetime_rejects_invalid_and_naive_values():
    with pytest.raises(InvalidProviderResponseError):
        finland._parse_datetime("not-a-timestamp")
    with pytest.raises(InvalidProviderResponseError, match="timezone-naive"):
        finland._parse_datetime("2026-01-01T00:00:00")


def test_optional_datetime_rejects_non_string():
    assert finland._optional_datetime(None, "field") is None
    with pytest.raises(InvalidProviderResponseError):
        finland._optional_datetime(123, "field")


def test_list_field_rejects_non_list():
    assert finland._list_field({}, "features") == []
    with pytest.raises(InvalidProviderResponseError):
        finland._list_field({"features": "not-a-list"}, "features")


def test_required_string_rejects_blank_and_non_string():
    with pytest.raises(InvalidProviderResponseError):
        finland._required_string("  ", "field")
    with pytest.raises(InvalidProviderResponseError):
        finland._required_string(123, "field")


def test_optional_string_rejects_non_string():
    assert finland._optional_string(None, "field") is None
    assert finland._optional_string("  ", "field") is None
    with pytest.raises(InvalidProviderResponseError):
        finland._optional_string(123, "field")


def test_string_mapping_rejects_non_dict_and_non_string_keys():
    with pytest.raises(InvalidProviderResponseError):
        finland._string_mapping("not-a-dict", "field")
    with pytest.raises(InvalidProviderResponseError):
        finland._string_mapping({1: "a"}, "field")


def test_validate_search_rejects_invalid_coordinates():
    with pytest.raises(UnsupportedLocationError):
        finland._validate_search(float("nan"), 25.0, 500, 10)


def test_validate_search_rejects_invalid_station_limit():
    with pytest.raises(UnsupportedLocationError, match="station limit"):
        finland._validate_search(60.0, 25.0, 500, 0)


@pytest.mark.asyncio
async def test_finland_station_tariff_incomplete_when_mixing_reported_and_unreported():
    evse = _evse(
        connectors=[
            _connector(tariff_ids=["t1"]),
            _connector(tariff_ids=[]),
        ]
    )
    session = FakeSession(
        _full_payloads(
            features=[_location_feature(evses=[evse])],
            tariffs=[_tariff(tariff_id="t1")],
            statuses=[],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.INCOMPLETE


@pytest.mark.asyncio
async def test_finland_tariff_element_date_restriction_marks_tariff_inactive():
    future_start = (
        datetime.now(UTC).date().replace(year=datetime.now(UTC).year + 1).isoformat()
    )
    session = FakeSession(
        _full_payloads(
            tariffs=[_tariff(restrictions={"startDate": future_start})],
        )
    )
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.INACTIVE
    assert result.nearby[0].tariff is None


@pytest.mark.asyncio
async def test_finland_tariff_mixes_conditional_and_unconditional_elements():
    tariff_record = _tariff()
    conditional_element = {
        "priceComponents": [{"type": "ENERGY", "price": 1.0, "vat": None}],
        "restrictions": {"minKwh": 1},
    }
    tariff_record["elements"].append(conditional_element)
    session = FakeSession(_full_payloads(tariffs=[tariff_record]))
    provider = _provider(session)

    result = await provider.async_get_stations(60.1699, 24.9384, 500, 10)

    assert result.nearby[0].tariff_status is TariffStatus.CONDITIONAL
    assert result.nearby[0].tariff is None


def test_unique_component_price_returns_none_when_gross_price_is_unresolved():
    components = [{"type": "ENERGY", "price": 0.5, "vat": None}]
    price, vat = finland._unique_component_price(components, "ENERGY", "NO")
    assert price is None
    assert vat is None


def test_unique_time_price_returns_none_when_gross_price_is_unresolved():
    components = [{"type": "TIME", "price": 1.2, "vat": None, "stepSize": 60}]
    assert finland._unique_time_price(components, "NO") is None
