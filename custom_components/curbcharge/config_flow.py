"""UI configuration and options flows for CurbCharge."""

from __future__ import annotations

import hashlib
import math
from typing import TYPE_CHECKING, Any, cast

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult as FlowResult
else:
    from homeassistant.data_entry_flow import FlowResult

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


def manual_location_schema(
    latitude: float | None = None, longitude: float | None = None
) -> vol.Schema:
    """Return the schema for manually entered coordinates."""
    return vol.Schema(
        {
            (
                vol.Required(CONF_LATITUDE)
                if latitude is None
                else vol.Required(CONF_LATITUDE, default=latitude)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-90,
                    max=90,
                    step="any",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            (
                vol.Required(CONF_LONGITUDE)
                if longitude is None
                else vol.Required(CONF_LONGITUDE, default=longitude)
            ): selector.NumberSelector(
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


def _show_form(
    flow: config_entries.ConfigFlow | config_entries.OptionsFlow,
    *,
    step_id: str,
    data_schema: vol.Schema,
    errors: dict[str, str] | None = None,
) -> FlowResult:
    """Show a voluptuous form across Home Assistant's flow type changes."""
    # HA 2026.10 types use probatio; voluptuous remains needed for HA 2024.12.
    return flow.async_show_form(
        step_id=step_id,
        data_schema=cast(Any, data_schema),
        errors=errors,
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
                    return _show_form(
                        self,
                        step_id="user",
                        data_schema=_location_source_schema(),
                        errors={"base": "location_not_configured"},
                    )
                self._latitude, self._longitude = location
                return await self.async_step_station_options()
            return await self.async_step_manual_location()

        return _show_form(self, step_id="user", data_schema=_location_source_schema())

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Allow a user to change the configured search location."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            return _show_form(
                self,
                step_id="reconfigure",
                data_schema=_location_source_schema(
                    entry.data.get(CONF_LOCATION_SOURCE, LOCATION_HOME)
                ),
            )

        self._location_source = user_input[CONF_LOCATION_SOURCE]
        if self._location_source == LOCATION_HOME:
            location = _home_location(self.hass)
            if location is None:
                return _show_form(
                    self,
                    step_id="reconfigure",
                    data_schema=_location_source_schema(LOCATION_HOME),
                    errors={"base": "location_not_configured"},
                )
            self._latitude, self._longitude = location
            return await self._async_save_reconfigured_location(entry)

        stored_latitude = entry.data.get(CONF_LATITUDE)
        stored_longitude = entry.data.get(CONF_LONGITUDE)
        current_location: tuple[float, float] | None
        if (
            entry.data.get(CONF_LOCATION_SOURCE) == LOCATION_MANUAL
            and isinstance(stored_latitude, (int, float))
            and not isinstance(stored_latitude, bool)
            and isinstance(stored_longitude, (int, float))
            and not isinstance(stored_longitude, bool)
            and _valid_coordinates(float(stored_latitude), float(stored_longitude))
        ):
            current_location = (float(stored_latitude), float(stored_longitude))
        else:
            current_location = _home_location(self.hass)
        self._latitude, self._longitude = (
            current_location if current_location is not None else (None, None)
        )
        return _show_form(
            self,
            step_id="reconfigure_manual_location",
            data_schema=manual_location_schema(self._latitude, self._longitude),
        )

    async def async_step_reconfigure_manual_location(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect replacement coordinates for a manual search location."""
        if user_input is None:
            return self.async_abort(reason="location_not_configured")

        latitude = float(user_input[CONF_LATITUDE])
        longitude = float(user_input[CONF_LONGITUDE])
        if not _valid_coordinates(latitude, longitude):
            return _show_form(
                self,
                step_id="reconfigure_manual_location",
                data_schema=manual_location_schema(latitude, longitude),
                errors={"base": "invalid_location"},
            )
        self._location_source = LOCATION_MANUAL
        self._latitude = latitude
        self._longitude = longitude
        return await self._async_save_reconfigured_location(
            self._get_reconfigure_entry()
        )

    async def _async_save_reconfigured_location(self, entry: ConfigEntry) -> FlowResult:
        """Validate a replacement location and update the existing entry."""
        assert self._location_source is not None
        assert self._latitude is not None
        assert self._longitude is not None

        options = {**entry.data, **entry.options}
        provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
        radius = int(options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M))
        try:
            provider = create_provider(provider_id, async_get_clientsession(self.hass))
            await provider.async_validate_connection(
                self._latitude, self._longitude, radius
            )
        except ChargingProviderError as err:
            if self._location_source == LOCATION_MANUAL:
                return _show_form(
                    self,
                    step_id="reconfigure_manual_location",
                    data_schema=manual_location_schema(self._latitude, self._longitude),
                    errors={"base": _flow_error(err)},
                )
            return _show_form(
                self,
                step_id="reconfigure",
                data_schema=_location_source_schema(LOCATION_HOME),
                errors={"base": _flow_error(err)},
            )

        new_unique_id = _config_unique_id(
            provider_id, self._latitude, self._longitude, radius
        )
        if new_unique_id != entry.unique_id:
            await self.async_set_unique_id(new_unique_id)
            self._abort_if_unique_id_configured()

        data = dict(entry.data)
        data[CONF_LOCATION_SOURCE] = self._location_source
        if self._location_source == LOCATION_MANUAL:
            data[CONF_LATITUDE] = self._latitude
            data[CONF_LONGITUDE] = self._longitude
        else:
            data.pop(CONF_LATITUDE, None)
            data.pop(CONF_LONGITUDE, None)
        return self.async_update_reload_and_abort(
            entry,
            unique_id=new_unique_id,
            data=data,
            reason="reconfigure_successful",
        )

    async def async_step_manual_location(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate manual coordinates."""
        if user_input is not None:
            latitude = float(user_input[CONF_LATITUDE])
            longitude = float(user_input[CONF_LONGITUDE])
            if not _valid_coordinates(latitude, longitude):
                return _show_form(
                    self,
                    step_id="manual_location",
                    data_schema=manual_location_schema(),
                    errors={"base": "invalid_location"},
                )
            self._latitude = latitude
            self._longitude = longitude
            return await self.async_step_station_options()

        return _show_form(
            self, step_id="manual_location", data_schema=manual_location_schema()
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
                return _show_form(
                    self,
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

        return _show_form(
            self, step_id="station_options", data_schema=station_options_schema()
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
        return _show_form(self, step_id="init", data_schema=options_schema(defaults))


def _location_source_schema(
    default: str = LOCATION_HOME,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LOCATION_SOURCE, default=default
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
