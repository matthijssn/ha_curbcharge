"""Async client for Eco-Movement's Belgium DATEX II API."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import time
from typing import TypedDict, cast
from urllib.parse import quote

import aiohttp

from .provider import (
    AuthenticationError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    retry_after_seconds,
)

_API_BASE_URL = "https://nap-be.eco-movement.com/datex2/v1"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
_LOCATION_CACHE_SECONDS = 23 * 60 * 60
_IP_REQUEST_INTERVAL = 10
_LOCATION_REQUEST_INTERVAL = 120
_EVSE_STATUS_INTERVAL = 30


class BelgiumDatexDocument(TypedDict, total=False):
    """DATEX II v3 publication envelopes returned by the Belgium API."""

    payload: dict[str, object]
    messageContainer: dict[str, object]


class BelgiumRequestLimiter:
    """Share documented Belgium endpoint and per-IP limits across clients."""

    def __init__(self) -> None:
        """Initialize limiter state for one Home Assistant instance."""
        self._lock = asyncio.Lock()
        self._last_request: float | None = None
        self._last_locations: dict[bytes, float] = {}
        self._last_evse_status: dict[tuple[bytes, str], float] = {}

    async def async_wait(self, token: str, evse_id: str | None = None) -> None:
        """Wait until this token, EVSE, and IP may safely make another request."""
        token_key = hashlib.sha256(token.encode("ascii")).digest()
        async with self._lock:
            now = time.monotonic()
            allowed_at = now
            if self._last_request is not None:
                allowed_at = max(allowed_at, self._last_request + _IP_REQUEST_INTERVAL)
            if evse_id is None:
                last_location = self._last_locations.get(token_key)
                if last_location is not None:
                    allowed_at = max(
                        allowed_at, last_location + _LOCATION_REQUEST_INTERVAL
                    )
            else:
                last_status = self._last_evse_status.get((token_key, evse_id))
                if last_status is not None:
                    allowed_at = max(allowed_at, last_status + _EVSE_STATUS_INTERVAL)

            delay = allowed_at - now
            if delay > 0:
                await asyncio.sleep(delay)

            requested_at = time.monotonic()
            self._last_request = requested_at
            if evse_id is None:
                self._last_locations[token_key] = requested_at
            else:
                self._last_evse_status[(token_key, evse_id)] = requested_at


class BelgiumDatexClient:
    """Fetch Belgium locations and per-EVSE DATEX II status documents."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        request_limiter: BelgiumRequestLimiter,
    ) -> None:
        """Initialize using Home Assistant's session and shared request limiter."""
        if (
            not token
            or not token.isascii()
            or any(not 33 <= ord(character) <= 126 for character in token)
        ):
            raise ValueError("The Belgium API token must be printable ASCII.")
        self._session = session
        self._token = token
        self._request_limiter = request_limiter
        self._locations: BelgiumDatexDocument | None = None
        self._locations_cached_at: float | None = None

    async def async_get_locations(self) -> BelgiumDatexDocument:
        """Return the static country snapshot, cached for less than one day."""
        now = time.monotonic()
        if (
            self._locations is not None
            and self._locations_cached_at is not None
            and now - self._locations_cached_at < _LOCATION_CACHE_SECONDS
        ):
            return copy.deepcopy(self._locations)

        locations = await self._async_request_json("/locations")
        self._locations = locations
        self._locations_cached_at = time.monotonic()
        return copy.deepcopy(locations)

    async def async_get_evse_status(self, evse_id: str) -> BelgiumDatexDocument:
        """Fetch current status and ad-hoc price for one EVSE."""
        if not evse_id:
            raise InvalidProviderResponseError("Belgium returned an empty EVSE id")
        return await self._async_request_json(
            f"/status/{quote(evse_id, safe='')}",
            evse_id=evse_id,
        )

    async def _async_request_json(
        self, path: str, *, evse_id: str | None = None
    ) -> BelgiumDatexDocument:
        """Request one documented endpoint without exposing credentials in errors."""
        await self._request_limiter.async_wait(self._token, evse_id)
        try:
            async with self._session.get(
                f"{_API_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=_REQUEST_TIMEOUT,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError(
                        "Eco-Movement rejected the Belgium API credentials"
                    )
                if response.status == 429:
                    raise ProviderRateLimitError(
                        "Eco-Movement rate-limited the Belgium API request",
                        retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if not 200 <= response.status < 300:
                    raise ProviderConnectionError(
                        "Eco-Movement returned an unsuccessful Belgium API response"
                    )
                body = await response.read()
        except (
            AuthenticationError,
            ProviderConnectionError,
            ProviderRateLimitError,
        ):
            raise
        except (TimeoutError, aiohttp.ClientError, OSError):
            raise ProviderConnectionError(
                "Could not connect to the Belgium charging-data provider"
            ) from None

        try:
            payload: object = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise InvalidProviderResponseError(
                "Eco-Movement returned invalid Belgium DATEX II JSON"
            ) from err
        if not isinstance(payload, dict) or any(
            not isinstance(key, str) for key in payload
        ):
            raise InvalidProviderResponseError(
                "Eco-Movement returned an invalid Belgium DATEX II document"
            )
        return cast(BelgiumDatexDocument, payload)
