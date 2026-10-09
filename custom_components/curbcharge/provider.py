"""Provider interface and errors."""

from abc import ABC, abstractmethod

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


class BaseChargingProvider(ABC):
    """Interface implemented by each upstream charging-data provider."""

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
