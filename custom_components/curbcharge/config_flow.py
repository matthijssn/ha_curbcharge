"""UI configuration and options flows for CurbCharge."""

from __future__ import annotations

import hashlib
import math
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import create_provider
from .const import (
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DEFAULT_INCLUDE_UNKNOWN,
    DEFAULT_MAX_STATIONS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_MANUAL,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SEARCH_RADIUS_M,
    PROVIDER_NDW_DOT_NL,
)
from .provider import (
    AuthenticationError,
    ChargingProviderError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
)


def manual_location_schema() -> vol.Schema:
    """Return the schema for manually entered coordinates."""
    return vol.Schema(
        {
            vol.Required(CONF_LATITUDE): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-90,
                    max=90,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_LONGITUDE): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-180,
                    max=180,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def station_options_schema(
    *,
    radius: int = DEFAULT_SEARCH_RADIUS_M,
    max_stations: int = DEFAULT_MAX_STATIONS,
) -> vol.Schema:
    """Return the initial provider and station-search schema."""
    return vol.Schema(
        {
            vol.Required(CONF_SEARCH_RADIUS, default=radius): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SEARCH_RADIUS_M,
                    max=MAX_SEARCH_RADIUS_M,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MAX_STATIONS, default=max_stations
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_MAX_STATIONS,
                    max=MAX_MAX_STATIONS,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_PROVIDER, default=PROVIDER_NDW_DOT_NL
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[PROVIDER_NDW_DOT_NL],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="provider",
                )
            ),
        }
    )


def options_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Return the editable runtime options schema."""
    return vol.Schema(
        {
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MINUTES),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_MINUTES,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_SEARCH_RADIUS,
                default=defaults.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_SEARCH_RADIUS_M,
                    max=MAX_SEARCH_RADIUS_M,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_MAX_STATIONS,
                default=defaults.get(CONF_MAX_STATIONS, DEFAULT_MAX_STATIONS),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_MAX_STATIONS,
                    max=MAX_MAX_STATIONS,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_PROVIDER,
                default=defaults.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[PROVIDER_NDW_DOT_NL],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="provider",
                )
            ),
            vol.Required(
                CONF_INCLUDE_UNKNOWN,
                default=defaults.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN),
            ): selector.BooleanSelector(),
        }
    )


class CurbChargeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Set up a CurbCharge location and validate the provider connection."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._location_source: str | None = None
        self._latitude: float | None = None
        self._longitude: float | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose Home Assistant or manual coordinates."""
        if user_input is not None:
            self._location_source = user_input[CONF_LOCATION_SOURCE]
            if self._location_source == LOCATION_HOME:
                location = _home_location(self.hass)
                if location is None:
                    return self.async_show_form(
                        step_id="user",
                        data_schema=_location_source_schema(),
                        errors={"base": "location_not_configured"},
                    )
                self._latitude, self._longitude = location
                return await self.async_step_station_options()
            return await self.async_step_manual_location()

        return self.async_show_form(
            step_id="user", data_schema=_location_source_schema()
        )

    async def async_step_manual_location(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate manual coordinates."""
        if user_input is not None:
            latitude = float(user_input[CONF_LATITUDE])
            longitude = float(user_input[CONF_LONGITUDE])
            if not _valid_coordinates(latitude, longitude):
                return self.async_show_form(
                    step_id="manual_location",
                    data_schema=manual_location_schema(),
                    errors={"base": "invalid_location"},
                )
            self._latitude = latitude
            self._longitude = longitude
            return await self.async_step_station_options()

        return self.async_show_form(
            step_id="manual_location", data_schema=manual_location_schema()
        )

    async def async_step_station_options(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure search limits and verify the provider before saving."""
        if user_input is not None:
            if (
                self._latitude is None
                or self._longitude is None
                or not _valid_coordinates(self._latitude, self._longitude)
            ):
                return self.async_abort(reason="location_not_configured")

            provider_id = user_input[CONF_PROVIDER]
            radius = int(user_input[CONF_SEARCH_RADIUS])
            max_stations = int(user_input[CONF_MAX_STATIONS])
            await self.async_set_unique_id(
                _config_unique_id(provider_id, self._latitude, self._longitude, radius)
            )
            self._abort_if_unique_id_configured()

            try:
                provider = create_provider(
                    provider_id, async_get_clientsession(self.hass)
                )
                await provider.async_validate_connection(
                    self._latitude, self._longitude, radius
                )
            except ChargingProviderError as err:
                return self.async_show_form(
                    step_id="station_options",
                    data_schema=station_options_schema(
                        radius=radius, max_stations=max_stations
                    ),
                    errors={"base": _flow_error(err)},
                )

            data: dict[str, Any] = {
                CONF_LOCATION_SOURCE: self._location_source,
                CONF_PROVIDER: provider_id,
                CONF_SEARCH_RADIUS: radius,
                CONF_MAX_STATIONS: max_stations,
            }
            if self._location_source == LOCATION_MANUAL:
                data[CONF_LATITUDE] = self._latitude
                data[CONF_LONGITUDE] = self._longitude
            return self.async_create_entry(title="CurbCharge", data=data)

        return self.async_show_form(
            step_id="station_options", data_schema=station_options_schema()
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CurbChargeOptionsFlow:
        """Create the options flow."""
        return CurbChargeOptionsFlow()


class CurbChargeOptionsFlow(config_entries.OptionsFlow):
    """Edit refresh and search behavior."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save options or show their current values."""
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data={
                    CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                    CONF_SEARCH_RADIUS: int(user_input[CONF_SEARCH_RADIUS]),
                    CONF_MAX_STATIONS: int(user_input[CONF_MAX_STATIONS]),
                    CONF_PROVIDER: user_input[CONF_PROVIDER],
                    CONF_INCLUDE_UNKNOWN: user_input[CONF_INCLUDE_UNKNOWN],
                },
            )

        defaults = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init", data_schema=options_schema(defaults)
        )


def _location_source_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LOCATION_SOURCE, default=LOCATION_HOME
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[LOCATION_HOME, LOCATION_MANUAL],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="location_source",
                )
            )
        }
    )


def _home_location(hass: HomeAssistant) -> tuple[float, float] | None:
    latitude = hass.config.latitude
    longitude = hass.config.longitude
    if latitude is None or longitude is None:
        return None
    latitude = float(latitude)
    longitude = float(longitude)
    if latitude == 0 and longitude == 0:
        return None
    return (latitude, longitude) if _valid_coordinates(latitude, longitude) else None


def _valid_coordinates(latitude: float, longitude: float) -> bool:
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _config_unique_id(
    provider: str, latitude: float, longitude: float, radius: int
) -> str:
    area_digest = hashlib.sha256(
        f"{latitude:.6f},{longitude:.6f},{radius}".encode()
    ).hexdigest()[:16]
    return f"{provider}_{area_digest}"


def _flow_error(error: ChargingProviderError) -> str:
    if isinstance(error, AuthenticationError):
        return "auth"
    if isinstance(error, ProviderRateLimitError):
        return "rate_limited"
    if isinstance(error, InvalidProviderResponseError):
        return "invalid_response"
    if isinstance(error, UnsupportedLocationError):
        return "invalid_location"
    if isinstance(error, UnsupportedProviderError):
        return "unsupported_provider"
    if isinstance(error, ProviderConnectionError):
        return "cannot_connect"
    return "cannot_connect"
