"""Provider interface and errors."""

import math
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from .models import ChargingStation


class ChargingProviderError(Exception):
    """Base exception for charging data providers."""


class AuthenticationError(ChargingProviderError):
    """The provider rejected authentication."""


class ProviderConnectionError(ChargingProviderError):
    """The provider could not be reached."""


class ProviderRateLimitError(ChargingProviderError):
    """The provider rate-limited a request."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        """Initialize a rate-limit error with an optional delay in seconds."""
        super().__init__(message)
        self.retry_after = retry_after


class InvalidProviderResponseError(ChargingProviderError):
    """The provider returned data that did not match its documented schema."""


class UnsupportedProviderError(ChargingProviderError):
    """The requested provider is not implemented."""


class UnsupportedLocationError(ChargingProviderError):
    """The provider cannot represent the requested search area."""


def retry_after_seconds(value: str | None) -> int | None:
    """Parse an HTTP Retry-After delta or date into non-negative seconds."""
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0, math.ceil((retry_at - datetime.now(UTC)).total_seconds()))


class BaseChargingProvider(ABC):
    """Interface implemented by each upstream charging-data provider."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Return the display name of the provider."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Return the stable provider identifier used in entity identities."""

    @property
    @abstractmethod
    def country_code(self) -> str:
        """Return the provider's ISO 3166-1 alpha-2 country code."""

    @abstractmethod
    async def async_get_stations(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        max_stations: int,
    ) -> tuple[ChargingStation, ...]:
        """Fetch stations within a radius."""

    @abstractmethod
    async def async_validate_connection(
        self, latitude: float, longitude: float, radius_m: int
    ) -> None:
        """Verify connectivity and response shape before creating an entry."""
