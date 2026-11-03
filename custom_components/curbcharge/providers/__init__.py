"""Factory that selects and constructs the configured charging provider."""

import aiohttp
from homeassistant.core import HomeAssistant

from ..const import (
    NOBIL_PROVIDER_IDS,
    PROVIDER_AFDC_US,
    PROVIDER_DIGITRAFFIC_AFIR_FI,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_ICH_TANKE_STROM_CH,
    PROVIDER_NDW_DOT_NL,
)
from .afdc import create_afdc_provider
from .base import AuthenticationError, BaseChargingProvider, UnsupportedProviderError
from .belgium import create_belgium_provider
from .finland import create_finland_provider
from .ndw_nl import create_ndw_nl_provider
from .nobil import create_nobil_provider
from .switzerland import create_switzerland_provider


def create_provider(
    provider_id: str,
    session: aiohttp.ClientSession,
    hass: HomeAssistant | None = None,
    token: str | None = None,
) -> BaseChargingProvider:
    """Return the requested implemented provider."""
    if provider_id == PROVIDER_AFDC_US:
        if not token:
            raise AuthenticationError("An AFDC API key is required")
        return create_afdc_provider(session, token, hass)
    if provider_id == PROVIDER_NDW_DOT_NL:
        return create_ndw_nl_provider(session, hass)
    if provider_id == PROVIDER_ECO_MOVEMENT_BE and hass is not None and token:
        return create_belgium_provider(hass, session, token)
    if provider_id in NOBIL_PROVIDER_IDS:
        if not token:
            raise AuthenticationError("A NOBIL API key is required")
        return create_nobil_provider(session, provider_id, token, hass)
    if provider_id == PROVIDER_DIGITRAFFIC_AFIR_FI:
        return create_finland_provider(session, hass)
    if provider_id == PROVIDER_ICH_TANKE_STROM_CH:
        return create_switzerland_provider(session, hass)
    raise UnsupportedProviderError("The configured charging provider is unsupported")
