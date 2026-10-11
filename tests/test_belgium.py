"""Tests for the Belgium DATEX II HTTP client and request limits."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.curbcharge import belgium
from custom_components.curbcharge.belgium import (
    BelgiumDatexClient,
    BelgiumRequestLimiter,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
)


class FakeResponse:
    """Small async response for endpoint contract tests."""

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

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    """Record authorized requests and return preloaded responses."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str]]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout: object,
    ) -> FakeResponse:
        self.requests.append((url, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _client(session: FakeSession, token: str = "test-token") -> BelgiumDatexClient:
    return BelgiumDatexClient(session, token, BelgiumRequestLimiter())


@pytest.mark.asyncio
async def test_locations_are_authorized_and_cached():
    session = FakeSession([FakeResponse({"payload": {"sites": []}})])
    client = _client(session)

    locations = await client.async_get_locations()
    locations["payload"]["modified"] = True
    cached = await client.async_get_locations()

    assert cached == {"payload": {"sites": []}}
    assert len(session.requests) == 1
    url, headers = session.requests[0]
    assert url == f"{belgium._API_BASE_URL}/locations"
    assert headers == {"Authorization": "Bearer test-token"}


@pytest.mark.asyncio
async def test_status_request_escapes_evse_id():
    session = FakeSession([FakeResponse({"messageContainer": {}})])
    client = _client(session)

    await client.async_get_evse_status("BE/EVSE 1")

    assert session.requests[0][0] == f"{belgium._API_BASE_URL}/status/BE%2FEVSE%201"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "error"),
    [
        (FakeResponse({}, status=401), AuthenticationError),
        (FakeResponse({}, status=403), AuthenticationError),
        (
            FakeResponse({}, status=429, headers={"Retry-After": "45"}),
            ProviderRateLimitError,
        ),
        (FakeResponse({}, status=503), ProviderConnectionError),
        (TimeoutError("token must not leak"), ProviderConnectionError),
        (FakeResponse(b"{", raw=True), InvalidProviderResponseError),
        (FakeResponse([], raw=False), InvalidProviderResponseError),
    ],
)
async def test_response_errors_are_typed_and_do_not_leak_tokens(response, error):
    token = "super-secret-token"
    client = _client(FakeSession([response]), token)

    with pytest.raises(error) as raised:
        await client.async_get_locations()

    assert token not in str(raised.value)
    if error is ProviderRateLimitError:
        assert raised.value.retry_after == 45


def test_api_token_must_be_printable_non_whitespace_ascii():
    for token in ("", "has space", "non-ascii-\u00e9", "has\nnewline"):
        with pytest.raises(ValueError):
            _client(FakeSession([]), token)


@pytest.mark.asyncio
async def test_request_limiter_enforces_ip_location_and_per_evse_limits(monkeypatch):
    current_time = [0.0]
    waits: list[float] = []

    def monotonic() -> float:
        return current_time[0]

    async def sleep(delay: float) -> None:
        waits.append(delay)
        current_time[0] += delay

    monkeypatch.setattr(belgium.time, "monotonic", monotonic)
    monkeypatch.setattr(belgium.asyncio, "sleep", sleep)
    limiter = BelgiumRequestLimiter()

    await limiter.async_wait("token", "evse-1")
    await limiter.async_wait("token", "evse-1")
    assert current_time[0] == 30

    await limiter.async_wait("token", "evse-2")
    assert current_time[0] == 40

    await limiter.async_wait("token")
    await limiter.async_wait("token")
    assert current_time[0] == 170
    assert waits == [30, 10, 10, 120]
