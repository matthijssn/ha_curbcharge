"""Offline tests for Via Lietuva's Lithuanian OCPI snapshot."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from types import SimpleNamespace

import pytest

import custom_components.curbcharge.providers.lithuania as lithuania
from custom_components.curbcharge.const import (
    COUNTRY_LT,
    DOMAIN,
    PROVIDER_VIA_LIETUVA_LT,
)
from custom_components.curbcharge.models import (
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
)

VILNIUS = (54.68, 25.22)


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self._body), size):
            yield self._body[offset : offset + size]


class SlowContent:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, _size: int):
        await asyncio.sleep(0.1)
        yield self._body


class FakeResponse:
    def __init__(
        self,
        payload: object | bytes = b"",
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
        self.content = FakeContent(self._body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class SlowResponse(FakeResponse):
    def __init__(self, payload: object) -> None:
        super().__init__(payload)
        self.content = SlowContent(self._body)


class FakeSession:
    def __init__(self, responses: list[FakeResponse | BaseException]) -> None:
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
        if isinstance(response, BaseException):
            raise response
        return response


def _connector(
    *,
    identifier: object = 101,
    standard: str = "IEC_62196_T2",
    power: object = 22000,
    tariff_ids: object = ("tariff-1",),
) -> dict[str, object]:
    return {
        "id": identifier,
        "standard": standard,
        "format": "SOCKET",
        "power_type": "AC_1_PHASE",
        "max_electric_power": power,
        "tariff_ids": list(tariff_ids) if isinstance(tariff_ids, tuple) else tariff_ids,
    }


def _evse(
    *,
    uid: object = 1,
    evse_id: str | None = None,
    status: object = "AVAILABLE",
    connectors: list[dict[str, object]] | None = None,
    last_updated: str | None = "2025-01-02T03:04:05",
    physical_reference: str | None = "Bay 1",
) -> dict[str, object]:
    return {
        "uid": uid,
        "evse_id": evse_id or f"LT*TST*E{uid}",
        "status": status,
        "connectors": connectors if connectors is not None else [_connector()],
        "physical_reference": physical_reference,
        "last_updated": last_updated,
    }


def _location(
    *,
    identifier: object = 1,
    party: object = "TST",
    country_code: str = "LT",
    country: str = "LTU",
    publish: object = True,
    name: str | None = "Synthetic site",
    latitude: object = "54.68",
    longitude: object = "25.22",
    status: object = "AVAILABLE",
    evses: list[dict[str, object]] | None = None,
    time_zone: str | None = "Europe/Vilnius",
    last_updated: str | None = "2025-01-02T03:04:05",
) -> dict[str, object]:
    return {
        "country_code": country_code,
        "party_id": party,
        "id": identifier,
        "publish": publish,
        "name": name,
        "address": "Example Street 1",
        "city": "Vilnius",
        "postal_code": "00000",
        "country": country,
        "coordinates": {"latitude": latitude, "longitude": longitude},
        "evses": evses if evses is not None else [_evse(status=status)],
        "operator": {"name": "Example Operator"},
        "time_zone": time_zone,
        "last_updated": last_updated,
    }


def _component(
    kind: str = "ENERGY",
    price: object = "0.4200",
    *,
    vat: object = None,
    step_size: object = 1,
) -> dict[str, object]:
    return {
        "type": kind,
        "price": price,
        "vat": vat,
        "step_size": step_size,
    }


def _tariff(
    *,
    identifier: str = "tariff-1",
    party: str = "TST",
    country: str = "LT",
    currency: str = "EUR",
    tax_included: object = "YES",
    vat: object = None,
    components: list[dict[str, object]] | None = None,
    restrictions: dict[str, object] | None = None,
    start: str | None = None,
    end: str | None = None,
    min_price: object = None,
    max_price: object = None,
) -> dict[str, object]:
    components = components if components is not None else [_component(vat=vat)]
    element: dict[str, object] = {"price_components": components}
    if restrictions is not None:
        element["restrictions"] = restrictions
    result: dict[str, object] = {
        "country_code": country,
        "party_id": party,
        "id": identifier,
        "currency": currency,
        "tax_included": tax_included,
        "elements": [element],
        "start_date_time": start,
        "end_date_time": end,
        "last_updated": "2025-01-02 03:04:05",
    }
    if min_price is not None:
        result["min_price"] = min_price
    if max_price is not None:
        result["max_price"] = max_price
    return result


@pytest.fixture(autouse=True)
def remove_test_network_delay(monkeypatch):
    monkeypatch.setattr(lithuania, "_MIN_REQUEST_INTERVAL_SECONDS", 0)


def _envelope(data: list[object]) -> dict[str, object]:
    return {"timestamp": "2026-11-04T09:00:00Z", "status_code": 1000, "data": data}


def _locations_response(
    locations: list[object],
    *,
    total: int = 1,
    link: str | None = None,
    status_code: int = 1000,
) -> FakeResponse:
    payload = _envelope(locations)
    payload["status_code"] = status_code
    headers = {"X-Total-Count": str(total)}
    if link is not None:
        headers["Link"] = link
    return FakeResponse(payload, headers=headers)


def _tariffs_response(tariffs: list[object]) -> FakeResponse:
    return FakeResponse(_envelope(tariffs))


def _provider(session: FakeSession, hass: object | None = None):
    provider = create_provider(PROVIDER_VIA_LIETUVA_LT, session, hass)
    assert isinstance(provider, lithuania.LithuaniaChargingProvider)
    return provider


def _responses(
    locations: list[object] | None = None,
    tariffs: list[object] | None = None,
) -> FakeSession:
    return FakeSession(
        [
            _locations_response(locations if locations is not None else [_location()]),
            _tariffs_response(tariffs if tariffs is not None else [_tariff()]),
        ]
    )


async def test_maps_evse_connector_ids_status_power_and_safe_tariff():
    tariff = _tariff(
        components=[
            _component("ENERGY", "0.42"),
            _component("FLAT", "0.10"),
            _component("TIME", "0.60", step_size=300),
        ]
    )
    location = _location(
        evses=[
            _evse(
                uid=1,
                connectors=[
                    _connector(
                        identifier=101,
                        standard="IEC_62196_T2_COMBO",
                        power=50000,
                    )
                ],
            )
        ]
    )
    session = _responses([location], [tariff])
    provider = _provider(session)
    result = await provider.async_get_stations(*VILNIUS, 500, 10)

    assert provider.provider_id == PROVIDER_VIA_LIETUVA_LT
    assert provider.provider_name == "Via Lietuva Lithuania"
    assert provider.country_code == COUNTRY_LT
    assert len(result.nearby) == 1
    station = result.nearby[0]
    assert station.provider_id == "LT*TST*1*1"
    assert station.evse_id == "LT*TST*E1"
    assert station.name == "Synthetic site (Bay 1)"
    assert station.address == "Example Street 1, 00000, Vilnius"
    assert station.operator == "Example Operator"
    assert station.country_code == COUNTRY_LT
    assert station.provider == PROVIDER_VIA_LIETUVA_LT
    assert station.operator_id == "LT*TST"
    assert station.location is not None
    assert station.location.provider_id == "LT*TST*1"
    assert station.location.country_code == COUNTRY_LT
    assert station.distance_m == 0
    assert station.evse_status is EVSEStatus.AVAILABLE
    assert station.availability_reported is True
    assert station.connector_count_reported is True
    assert station.total_connectors == 1
    assert station.available_connectors is None
    assert station.has_available_connectors is True
    assert station.last_updated is None
    assert station.last_updated_source is None
    assert station.provider_data_age_seconds(datetime.now(UTC)) is None

    connector = station.connectors[0]
    assert connector.provider_connector_id == "LT*TST*1*1:connector:101"
    assert connector.connector_type == "IEC_62196_T2_COMBO"
    assert connector.connector_type_id is ConnectorTypeId.CCS_COMBO_2
    assert connector.connector_format == "SOCKET"
    assert connector.power_type == "AC_1_PHASE"
    assert connector.max_power_kw == 50
    assert connector.status is ConnectorStatus.UNKNOWN
    assert connector.available_count is None
    assert connector.tariff_ids == ("tariff-1",)
    assert connector.tariff_status is TariffStatus.AVAILABLE

    assert station.tariff is not None
    assert station.tariff.price_per_kwh == Decimal("0.42")
    assert station.tariff.session_fee == Decimal("0.10")
    assert station.tariff.price_per_minute == Decimal("0.01")
    assert station.tariff.vat_percentage is None
    assert station.tariff_status is TariffStatus.AVAILABLE
    assert station.tariff.source == "Via Lietuva Lithuania"

    assert len(session.requests) == 2
    assert session.requests[0][0].endswith("/locations")
    assert session.requests[0][1] == {"limit": "1000", "offset": "0"}
    assert session.requests[1][0].endswith("/tariffs")
    assert session.requests[0][2]["Accept"] == "application/json"
    assert session.requests[0][2]["User-Agent"] == "CurbCharge"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AVAILABLE", EVSEStatus.AVAILABLE),
        ("BLOCKED", EVSEStatus.BLOCKED),
        ("CHARGING", EVSEStatus.CHARGING),
        ("INOPERATIVE", EVSEStatus.INOPERATIVE),
        ("OUTOFORDER", EVSEStatus.OUT_OF_ORDER),
        ("PLANNED", EVSEStatus.PLANNED),
        ("REMOVED", EVSEStatus.REMOVED),
        ("RESERVED", EVSEStatus.RESERVED),
        ("UNKNOWN", EVSEStatus.UNKNOWN),
        ("FUTURE_STATUS", EVSEStatus.UNKNOWN),
        (None, EVSEStatus.UNKNOWN),
    ],
)
def test_maps_evse_statuses(raw, expected):
    assert lithuania._evse_status(raw) is expected


def test_removed_evse_is_excluded_and_status_does_not_invent_connector_counts():
    locations = _location(
        evses=[
            _evse(uid=535, status="REMOVED"),
            _evse(uid=536, status="CHARGING"),
            _evse(uid=537, status="UNKNOWN"),
        ]
    )
    stations = lithuania._stations_from_location(
        locations,
        *VILNIUS,
        lithuania._index_tariffs(()),
        datetime.now(UTC),
    )
    assert [station.evse_id for station in stations] == [
        "LT*TST*E536",
        "LT*TST*E537",
    ]
    assert stations[0].evse_status is EVSEStatus.CHARGING
    assert stations[0].has_available_connectors is False
    assert stations[0].available_connectors is None
    assert stations[0].occupied_connectors is None
    assert stations[1].evse_status is EVSEStatus.UNKNOWN
    assert stations[1].availability_reported is False
    assert stations[1].has_available_connectors is None


def test_unknown_connector_standards_are_preserved():
    location = _location(
        evses=[
            _evse(
                connectors=[
                    _connector(
                        standard="FUTURE_STANDARD",
                        tariff_ids=(),
                    )
                ]
            )
        ]
    )
    station = lithuania._stations_from_location(
        location, *VILNIUS, lithuania._index_tariffs(()), datetime.now(UTC)
    )[0]
    assert station.connectors[0].connector_type == "FUTURE_STANDARD"
    assert station.connectors[0].connector_type_id is None
    assert station.connectors[0].tariff_status is TariffStatus.NOT_REPORTED
    assert station.tariff_status is TariffStatus.NOT_REPORTED


async def test_short_pages_are_not_terminal_and_bad_links_are_ignored():
    locations = [
        _location(identifier=1),
        _location(identifier=2),
        _location(identifier=3, country="DEU"),
    ]
    session = FakeSession(
        [
            _locations_response([locations[0]], total=1001, link='<bad>; rel="next"'),
            _locations_response(
                [locations[1], locations[2]], total=1001, link="not a link"
            ),
            _tariffs_response([]),
        ]
    )
    result = await _provider(session).async_get_stations(*VILNIUS, 1000, 10)
    assert [station.provider_id for station in result.nearby] == [
        "LT*TST*1*1",
        "LT*TST*2*1",
    ]
    assert [call[1]["offset"] for call in session.requests[:2]] == ["0", "1000"]
    assert len(session.requests) == 3


async def test_full_location_page_count_and_terminal_window():
    pages = [
        _locations_response([_location(identifier=1)], total=2001),
        _locations_response([], total=2001),
        _locations_response([_location(identifier=2)], total=2001),
        _tariffs_response([]),
    ]
    session = FakeSession(pages)
    result = await _provider(session).async_get_stations(*VILNIUS, 1000, 10)
    assert len(result.nearby) == 2
    assert [call[1]["offset"] for call in session.requests[:3]] == [
        "0",
        "1000",
        "2000",
    ]


@pytest.mark.parametrize(
    ("responses", "message"),
    [
        (
            [
                _locations_response(
                    [{"id": 1, "party_id": "P", "country": "LTU"}], total=1
                )
            ],
            "country_code",
        ),
        (
            [
                _locations_response([_location(identifier=1)], total=1001),
                _locations_response([_location(identifier=1)], total=1001),
            ],
            "repeated a location",
        ),
        (
            [
                _locations_response([_location(identifier=1)], total=1001),
                _locations_response([_location(identifier="1")], total=1001),
            ],
            "repeated a location",
        ),
        (
            [
                _locations_response([_location(identifier=1)], total=1001),
                _locations_response([_location(identifier=2)], total=1002),
            ],
            "total changed",
        ),
        (
            [
                FakeResponse(
                    _envelope([_location()]),
                    headers={},
                )
            ],
            "total count",
        ),
        (
            [
                _locations_response([_location()], total=20001),
            ],
            "page bound",
        ),
        (
            [
                _locations_response([_location()], total=0),
            ],
            "zero total",
        ),
        (
            [
                _locations_response([_location(publish="yes")], total=1),
            ],
            "publish flag",
        ),
        (
            [
                _locations_response([_location(country_code="L")], total=1),
            ],
            "country_code",
        ),
    ],
)
async def test_invalid_location_pagination_fails_without_partial_success(
    responses, message
):
    session = FakeSession(responses)
    with pytest.raises(InvalidProviderResponseError, match=message):
        await _provider(session).async_get_stations(*VILNIUS, 1000, 10)
    assert not any(call[0].endswith("/tariffs") for call in session.requests)


@pytest.mark.parametrize(
    ("status_code", "exception"),
    [
        (401, ProviderConnectionError),
        (403, ProviderConnectionError),
        (503, ProviderConnectionError),
    ],
)
async def test_http_errors_are_explicit(status_code, exception):
    session = FakeSession([FakeResponse(status=status_code)])
    with pytest.raises(exception, match=f"HTTP {status_code}"):
        await _provider(session).async_validate_connection(*VILNIUS, 500)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(b"{not json"),
        FakeResponse({"status_code": 2000, "data": []}),
        FakeResponse({"status_code": True, "data": []}),
        FakeResponse({"status_code": 1000}),
        FakeResponse({"status_code": 1000, "data": "not a list"}),
    ],
)
async def test_invalid_ocpi_responses_are_rejected(response):
    session = FakeSession([response])
    with pytest.raises(InvalidProviderResponseError):
        await _provider(session).async_validate_connection(*VILNIUS, 500)


async def test_tariff_link_header_is_not_silently_truncated():
    session = FakeSession(
        [
            _locations_response([_location()], total=1),
            FakeResponse(
                _envelope([]),
                headers={"Link": '</tariffs?offset=1>; rel="next"'},
            ),
        ]
    )
    with pytest.raises(InvalidProviderResponseError, match="requires pagination"):
        await _provider(session).async_get_stations(*VILNIUS, 500, 10)


async def test_shared_snapshot_validation_and_exact_location_floor(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(lithuania.asyncio, "sleep", sleep)
    session = _responses()
    hass = SimpleNamespace(data={})
    first = _provider(session, hass)
    second = _provider(session, hass)

    await asyncio.gather(
        first.async_validate_connection(*VILNIUS, 500),
        second.async_get_stations(*VILNIUS, 500, 10),
    )
    assert len(session.requests) == 2
    assert second._snapshot is first._snapshot
    await second.async_get_stations(*VILNIUS, 500, 10)

    now[0] += 299
    await second.async_get_stations(*VILNIUS, 500, 10)
    assert len(session.requests) == 2
    now[0] += 1
    session.responses.append(_locations_response([_location()], total=1))
    await second.async_get_stations(*VILNIUS, 500, 10)
    assert len(session.requests) == 3


async def test_tariffs_refresh_after_fifteen_minutes_without_refetching_locations(
    monkeypatch,
):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(lithuania, "_MIN_REQUEST_INTERVAL_SECONDS", 1.0)

    async def sleep(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(lithuania.asyncio, "sleep", sleep)
    session = _responses()
    provider = _provider(session)
    await provider.async_get_stations(*VILNIUS, 500, 10)
    for advance in (299, 300, 300):
        now[0] += advance
        session.responses.append(_locations_response([_location()], total=1))
        await provider.async_get_stations(*VILNIUS, 500, 10)
    now[0] += 1
    session.responses.append(_tariffs_response([_tariff()]))
    await provider.async_get_stations(*VILNIUS, 500, 10)
    assert [call[0].rsplit("/", 1)[-1] for call in session.requests] == [
        "locations",
        "tariffs",
        "locations",
        "locations",
        "locations",
        "tariffs",
    ]


async def test_inter_page_and_component_requests_are_paced(monkeypatch):
    now = [1000.0]
    delays: list[float] = []
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(lithuania, "_MIN_REQUEST_INTERVAL_SECONDS", 1.0)

    async def sleep(seconds: float) -> None:
        delays.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(lithuania.asyncio, "sleep", sleep)
    session = FakeSession(
        [
            _locations_response([_location()], total=1001),
            _locations_response([_location(identifier=2)], total=1001),
            _tariffs_response([]),
        ]
    )
    await _provider(session).async_validate_connection(*VILNIUS, 500)
    assert delays == [1.0, 1.0]


async def test_failed_tariff_refresh_never_serves_old_success(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    session = _responses()
    provider = _provider(session)
    await provider.async_get_stations(*VILNIUS, 500, 10)
    now[0] += 900
    session.responses.extend(
        [
            _locations_response([_location()], total=1),
            FakeResponse(status=503),
        ]
    )
    with pytest.raises(ProviderConnectionError, match="HTTP 503"):
        await provider.async_get_stations(*VILNIUS, 500, 10)
    with pytest.raises(ProviderConnectionError, match="HTTP 503"):
        await provider.async_get_stations(*VILNIUS, 500, 10)
    assert len(session.requests) == 4


@pytest.mark.parametrize(
    ("header", "expected"),
    [(None, 300), ("0", 300), ("12", 300), ("720", 720), ("bad", 300)],
)
async def test_429_uses_retry_after_or_bounded_fallback(monkeypatch, header, expected):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    session = FakeSession(
        [
            FakeResponse(
                status=429,
                headers={} if header is None else {"Retry-After": header},
            )
        ]
    )
    with pytest.raises(ProviderRateLimitError) as error:
        await _provider(session).async_validate_connection(*VILNIUS, 500)
    assert error.value.retry_after == expected


async def test_429_http_date_is_supported(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    retry = format_datetime(datetime.now(UTC) + timedelta(seconds=900), usegmt=True)
    session = FakeSession([FakeResponse(status=429, headers={"Retry-After": retry})])
    with pytest.raises(ProviderRateLimitError) as error:
        await _provider(session).async_validate_connection(*VILNIUS, 500)
    assert 899 <= error.value.retry_after <= 900


async def test_rate_limit_cooldown_is_shared_and_escalates(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    hass = SimpleNamespace(data={})
    session = FakeSession([FakeResponse(status=429), FakeResponse(status=429)])
    first = _provider(session, hass)
    second = _provider(FakeSession([]), hass)
    with pytest.raises(ProviderRateLimitError) as error:
        await first.async_validate_connection(*VILNIUS, 500)
    assert error.value.retry_after == 300
    with pytest.raises(ProviderRateLimitError) as blocked:
        await second.async_get_stations(*VILNIUS, 500, 10)
    assert blocked.value.retry_after == 300
    now[0] += 300
    with pytest.raises(ProviderRateLimitError) as next_error:
        await first.async_validate_connection(*VILNIUS, 500)
    assert next_error.value.retry_after == 600


async def test_fallback_cooldown_is_bounded(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    session = FakeSession([FakeResponse(status=429) for _ in range(6)])
    provider = _provider(session)
    for delay in (300, 600, 1200, 2400, 3600, 3600):
        with pytest.raises(ProviderRateLimitError) as error:
            await provider.async_validate_connection(*VILNIUS, 500)
        assert error.value.retry_after == delay
        now[0] += delay


async def test_cancelled_refresh_blocks_stale_success_until_retry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(lithuania.time, "monotonic", lambda: now[0])
    provider = _provider(FakeSession([asyncio.CancelledError()]))
    with pytest.raises(asyncio.CancelledError):
        await provider.async_get_stations(*VILNIUS, 500, 10)
    with pytest.raises(ProviderConnectionError, match="did not complete"):
        await provider.async_get_stations(*VILNIUS, 500, 10)


async def test_whole_refresh_timeout_fails_without_partial_snapshot(monkeypatch):
    monkeypatch.setattr(lithuania, "_SNAPSHOT_TIMEOUT_SECONDS", 0.001)
    provider = _provider(FakeSession([SlowResponse(_envelope([_location()]))]))
    with pytest.raises(ProviderConnectionError, match="timed out"):
        await provider.async_get_stations(*VILNIUS, 500, 10)
    with pytest.raises(ProviderConnectionError, match="timed out"):
        await provider.async_get_stations(*VILNIUS, 500, 10)


@pytest.mark.parametrize(
    ("tax_included", "vat", "expected", "status"),
    [
        ("YES", None, Decimal("0.42"), TariffStatus.AVAILABLE),
        ("N/A", None, Decimal("0.42"), TariffStatus.AVAILABLE),
        ("NO", "21", Decimal("0.5082"), TariffStatus.AVAILABLE),
        ("NO", None, None, TariffStatus.INCOMPLETE),
        (None, "21", None, TariffStatus.INCOMPLETE),
    ],
)
def test_gross_price_requires_explicit_tax_semantics(
    tax_included, vat, expected, status
):
    tariff = lithuania._parse_tariff(
        _tariff(tax_included=tax_included, vat=vat),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert tariff.price_per_kwh == expected
    assert tariff.status is status


def test_time_component_is_hourly_and_step_size_only_affects_billing():
    tariff = lithuania._parse_tariff(
        _tariff(
            components=[_component("TIME", "0.60", step_size=300)],
        ),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert tariff.price_per_minute == Decimal("0.01")
    assert tariff.status is TariffStatus.INCOMPLETE


def test_parking_time_is_not_mislabeled_as_charging_time():
    tariff = lithuania._parse_tariff(
        _tariff(
            components=[
                _component("ENERGY", "0.42"),
                _component("PARKING_TIME", "1.20", step_size=60),
            ]
        ),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert tariff.price_per_kwh == Decimal("0.42")
    assert tariff.price_per_minute is None
    assert tariff.status is TariffStatus.INCOMPLETE


def test_structured_price_is_not_replaced_by_conflicting_marketing_text():
    raw = _tariff()
    raw["tariff_alt_text"] = [{"language": "en", "text": "EUR 0.30/kWh"}]
    tariff = lithuania._parse_tariff(
        raw, datetime(2026, 11, 4, tzinfo=UTC), "Europe/Vilnius"
    )
    assert tariff.price_per_kwh == Decimal("0.4200")
    assert tariff.description is None


def test_tariff_unknown_components_caps_and_multiple_values_are_explicit():
    capped = lithuania._parse_tariff(
        _tariff(min_price={"after_taxes": 1}),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert capped.price_per_kwh == Decimal("0.4200")
    assert capped.status is TariffStatus.INCOMPLETE

    unknown = lithuania._parse_tariff(
        _tariff(
            components=[
                _component("ENERGY", "0.42"),
                _component("RESERVATION_TIME", "1.00"),
            ]
        ),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert unknown.price_per_kwh == Decimal("0.42")
    assert unknown.status is TariffStatus.INCOMPLETE

    ambiguous = lithuania._parse_tariff(
        _tariff(
            components=[
                _component("ENERGY", "0.42"),
                _component("ENERGY", "0.43"),
            ]
        ),
        datetime(2026, 11, 4, tzinfo=UTC),
        "Europe/Vilnius",
    )
    assert ambiguous.price_per_kwh is None
    assert ambiguous.status is TariffStatus.AMBIGUOUS


def test_tariff_windows_and_restrictions_never_assume_timezone():
    now = datetime(2026, 11, 4, 12, tzinfo=UTC)
    future = lithuania._parse_tariff(
        _tariff(start="2026-11-05T00:00:00Z"), now, "Europe/Vilnius"
    )
    assert future.status is TariffStatus.INACTIVE
    naive = lithuania._parse_tariff(
        _tariff(start="2026-11-05T00:00:00"), now, "Europe/Vilnius"
    )
    assert naive.price_per_kwh is None
    assert naive.status is TariffStatus.CONDITIONAL

    before = lithuania._parse_tariff(
        _tariff(restrictions={"start_date": "2026-11-05"}), now, "Europe/Vilnius"
    )
    assert before.status is TariffStatus.INACTIVE
    no_zone = lithuania._parse_tariff(
        _tariff(restrictions={"start_date": "2026-11-05"}), now, None
    )
    assert no_zone.status is TariffStatus.CONDITIONAL
    time_restricted = lithuania._parse_tariff(
        _tariff(restrictions={"start_time": "12:00"}), now, "Europe/Vilnius"
    )
    assert time_restricted.status is TariffStatus.CONDITIONAL


@pytest.mark.parametrize(
    ("raw", "field"),
    [
        (_tariff(tax_included="MAYBE"), "tax_included"),
        (_tariff(components=[_component("ENERGY", True)]), "price"),
        (_tariff(components=[_component("ENERGY", -1)]), "negative"),
        (_tariff(components=[_component("ENERGY", "NaN")]), "invalid"),
        (
            _tariff(components=[_component("TIME", "0.60", step_size=0)]),
            "step_size",
        ),
    ],
)
def test_invalid_tariffs_fail_explicitly(raw, field):
    with pytest.raises(InvalidProviderResponseError, match=field):
        lithuania._parse_tariff(
            raw, datetime(2026, 11, 4, tzinfo=UTC), "Europe/Vilnius"
        )


def test_tariff_lookup_uses_exact_namespace_then_only_unique_same_country_id():
    records = (
        _tariff(identifier="exact", party="TST"),
        _tariff(identifier="global", party="441"),
        _tariff(identifier="duplicate", party="441"),
        _tariff(identifier="duplicate", party="OTHER"),
        _tariff(identifier="foreign", party="TST", country="DE"),
    )
    index = lithuania._index_tariffs(records)
    loc = _location(
        evses=[
            _evse(
                connectors=[
                    _connector(identifier=1, tariff_ids=("exact",)),
                    _connector(identifier=2, tariff_ids=("global",)),
                    _connector(identifier=3, tariff_ids=("duplicate",)),
                    _connector(identifier=4, tariff_ids=("missing",)),
                    _connector(identifier=5, tariff_ids=("foreign",)),
                ]
            )
        ]
    )
    stations = lithuania._stations_from_location(
        loc, *VILNIUS, index, datetime(2026, 11, 4, tzinfo=UTC)
    )
    connectors = stations[0].connectors
    assert connectors[0].tariff_status is TariffStatus.AVAILABLE
    assert connectors[1].tariff_status is TariffStatus.AVAILABLE
    assert connectors[1].tariffs[0].tariff_id == "global"
    assert connectors[2].tariff_status is TariffStatus.AMBIGUOUS
    assert connectors[3].tariff_status is TariffStatus.MISSING
    assert connectors[4].tariff_status is TariffStatus.MISSING


def test_duplicate_tariff_key_is_ambiguous():
    index = lithuania._index_tariffs((_tariff(), _tariff()))
    station = lithuania._stations_from_location(
        _location(),
        *VILNIUS,
        index,
        datetime(2026, 11, 4, tzinfo=UTC),
    )[0]
    assert station.connectors[0].tariff_status is TariffStatus.AMBIGUOUS


def test_station_price_is_not_aggregated_across_missing_or_different_connectors():
    location = _location(
        evses=[
            _evse(
                connectors=[
                    _connector(identifier=1, tariff_ids=("tariff-1",)),
                    _connector(identifier=2, tariff_ids=("missing",)),
                ]
            )
        ]
    )
    station = lithuania._stations_from_location(
        location,
        *VILNIUS,
        lithuania._index_tariffs((_tariff(),)),
        datetime(2026, 11, 4, tzinfo=UTC),
    )[0]
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.MISSING

    different_rates = (
        _tariff(identifier="a", components=[_component("ENERGY", "0.42")]),
        _tariff(identifier="b", components=[_component("ENERGY", "0.43")]),
    )
    location["evses"] = [
        _evse(
            connectors=[
                _connector(identifier=3, tariff_ids=("a",)),
                _connector(identifier=4, tariff_ids=("b",)),
            ]
        )
    ]
    station = lithuania._stations_from_location(
        location,
        *VILNIUS,
        lithuania._index_tariffs(different_rates),
        datetime(2026, 11, 4, tzinfo=UTC),
    )[0]
    assert station.tariff is None
    assert station.tariff_status is TariffStatus.AMBIGUOUS


def test_datetime_parser_preserves_only_timezone_aware_values():
    aware = lithuania._aware_datetime("2026-11-04T10:00:00+02:00", "last_updated")
    assert aware == datetime(2026, 11, 4, 10, tzinfo=UTC).replace(hour=8)
    assert lithuania._aware_datetime("2026-11-04T10:00:00", "last_updated") is None
    with pytest.raises(InvalidProviderResponseError):
        lithuania._aware_datetime("not a timestamp", "last_updated")


def test_source_timestamp_provenance_never_claims_dynamic_status_age():
    station = lithuania._stations_from_location(
        _location(
            evses=[
                _evse(last_updated="2026-11-04T10:00:00Z"),
            ]
        ),
        *VILNIUS,
        lithuania._index_tariffs(()),
        datetime.now(UTC),
    )[0]
    assert station.last_updated_source is ProviderTimestampSource.VIA_LIETUVA_RECORD
    assert station.provider_data_age_seconds(datetime.now(UTC)) is None


@pytest.mark.parametrize(
    "coordinates",
    [
        {"latitude": "NaN", "longitude": "25.2"},
        {"latitude": "91", "longitude": "25.2"},
        {"latitude": "54.6"},
        None,
    ],
)
def test_invalid_coordinates_are_explicit(coordinates):
    location = _location()
    location["coordinates"] = coordinates
    with pytest.raises(InvalidProviderResponseError, match="coordinates"):
        lithuania._stations_from_location(
            location,
            *VILNIUS,
            lithuania._index_tariffs(()),
            datetime.now(UTC),
        )


@pytest.mark.parametrize(
    "location",
    [
        _location(country="LVA"),
        _location(publish=False),
    ],
)
async def test_country_and_publication_filtering(location):
    # Non-Lithuanian/unpublished records occupy raw offset space but are not
    # normalized into stations.
    session = FakeSession(
        [
            _locations_response([location], total=1),
            _tariffs_response([]),
        ]
    )
    result = await _provider(session).async_get_stations(*VILNIUS, 500, 10)
    assert result.nearby == ()
    assert result.favourites == ()


@pytest.mark.parametrize(
    "args",
    [
        (float("nan"), 25, 500, 10),
        (True, 25, 500, 10),
        (91, 25, 500, 10),
        (54, 181, 500, 10),
        (*VILNIUS, 99, 10),
        (*VILNIUS, 10001, 10),
        (*VILNIUS, True, 10),
        (*VILNIUS, 500, 0),
        (*VILNIUS, 500, 51),
        (*VILNIUS, 500, True),
    ],
)
async def test_search_bounds_are_validated_without_request(args):
    session = FakeSession([])
    with pytest.raises(UnsupportedLocationError):
        await _provider(session).async_get_stations(*args)
    assert not session.requests


async def test_radius_limit_favourites_unknown_and_distance_recalculation():
    locations = [
        _location(
            identifier=1,
            evses=[_evse(uid=1, status="UNKNOWN")],
        ),
        _location(
            identifier=2,
            latitude="55.674557",
            longitude="25.224355",
            evses=[_evse(uid=2, status="AVAILABLE")],
        ),
    ]
    session = FakeSession(
        [
            _locations_response(locations, total=2),
            _tariffs_response([]),
        ]
    )
    provider = _provider(session)
    favourite = FavouriteStationReference(
        PROVIDER_VIA_LIETUVA_LT, "LT*TST*2*2", "charger", 55.67, 25.22
    )
    result = await provider.async_get_stations(
        *VILNIUS, 100, 1, (favourite,), include_unknown=False
    )
    assert result.nearby == ()
    assert [station.provider_id for station in result.favourites] == ["LT*TST*2*2"]
    moved = await provider.async_get_stations(55.674557, 25.224355, 100, 1)
    assert moved.nearby[0].provider_id == "LT*TST*2*2"
    assert moved.nearby[0].distance_m == 0
    assert len(session.requests) == 2
    with pytest.raises(InvalidProviderResponseError, match="different provider"):
        await provider.async_get_stations(
            *VILNIUS,
            100,
            1,
            (FavouriteStationReference("ndw_dot_nl", "id", "n", *VILNIUS),),
        )


async def test_response_byte_budget_is_enforced_while_streaming():
    budget = lithuania._RefreshBudget(maximum=4)
    await budget.read(FakeResponse(b"123"))
    with pytest.raises(InvalidProviderResponseError, match="size budget"):
        await budget.read(FakeResponse(b"456"))
    assert budget.used == 3


def test_duplicate_connector_ids_are_rejected_after_normalization():
    location = _location(
        evses=[
            _evse(
                connectors=[
                    _connector(identifier=1),
                    _connector(identifier="1"),
                ]
            )
        ]
    )
    with pytest.raises(InvalidProviderResponseError, match="duplicate connector IDs"):
        lithuania._stations_from_location(
            location,
            *VILNIUS,
            lithuania._index_tariffs(()),
            datetime.now(UTC),
        )


async def test_network_failure_is_generic_and_rate_limited():
    session = FakeSession([OSError("private host details")])
    provider = _provider(session)
    with pytest.raises(ProviderConnectionError, match="Unable to fetch"):
        await provider.async_validate_connection(*VILNIUS, 500)


async def test_hass_snapshot_type_mismatch_is_explicit():
    hass = SimpleNamespace(data={DOMAIN: {lithuania._SNAPSHOT_DATA_KEY: "bad"}})
    with pytest.raises(RuntimeError, match="invalid type"):
        _provider(FakeSession([]), hass)


def test_identifier_and_country_validation():
    assert lithuania._identifier(535, "id") == "535"
    assert lithuania._identifier("  EVSE-1 ", "id") == "EVSE-1"
    assert lithuania._country_code("lt", "country") == "LT"
    for value in (True, 0, -1, 1.0, None, ""):
        with pytest.raises(InvalidProviderResponseError):
            lithuania._identifier(value, "id")


def test_invalid_tariff_objects_and_location_records_fail():
    with pytest.raises(InvalidProviderResponseError, match="not an object"):
        lithuania._mapping([], "tariff")
    with pytest.raises(InvalidProviderResponseError, match="no EVSE list"):
        lithuania._stations_from_location(
            {
                "country_code": "LT",
                "party_id": "P",
                "id": 1,
                "country": "LTU",
                "coordinates": {"latitude": "54.68", "longitude": "25.22"},
            },
            *VILNIUS,
            lithuania._index_tariffs(()),
            datetime.now(UTC),
        )
