"""UI configuration and options flows for CurbCharge."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import asdict
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
from .belgium import is_valid_belgium_api_token
from .const import (
    CONF_API_TOKEN,
    CONF_CONNECTOR_TYPES,
    CONF_FAVOURITES,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_MOVEMENT_THRESHOLD,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_MIN_POWER_KW,
    CONF_PROVIDER,
    CONF_REQUIRE_AVAILABLE,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DEFAULT_INCLUDE_UNKNOWN,
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DEFAULT_MAX_STATIONS,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DEFAULT_SEARCH_RADIUS_M,
    DOMAIN,
    LOCATION_ENTITY,
    LOCATION_HOME,
    LOCATION_MANUAL,
    MAX_MAX_STATIONS,
    MAX_SEARCH_RADIUS_M,
    MIN_MAX_STATIONS,
    MIN_SCAN_INTERVAL_MINUTES,
    MIN_SEARCH_RADIUS_M,
    NDW_CONNECTOR_TYPE_CODES,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from .coordinator import CurbChargeCoordinator
from .location import resolve_location, valid_coordinates
from .models import (
    ChargingStation,
    DiscoveryFilterSettings,
    FavouriteStationReference,
    parse_favourite_references,
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


def entity_location_schema(entity_id: str | None = None) -> vol.Schema:
    """Return a generic Home Assistant entity selector for location sources."""
    key = (
        vol.Required(CONF_LOCATION_ENTITY)
        if entity_id is None
        else vol.Required(CONF_LOCATION_ENTITY, default=entity_id)
    )
    return vol.Schema(
        {
            key: selector.EntitySelector(selector.EntitySelectorConfig()),
        }
    )


def belgium_credentials_schema() -> vol.Schema:
    """Return a password field for the Belgium provider token."""
    return vol.Schema(
        {
            vol.Required(CONF_API_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            )
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
                    options=[PROVIDER_NDW_DOT_NL, PROVIDER_ECO_MOVEMENT_BE],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="provider",
                )
            ),
        }
    )


def _discovery_filter_fields(
    settings: DiscoveryFilterSettings | None = None,
) -> dict[Any, Any]:
    """Return translated NDW-only discovery filter fields."""
    settings = settings or DiscoveryFilterSettings()
    return {
        vol.Required(
            CONF_CONNECTOR_TYPES, default=list(settings.connector_types)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(NDW_CONNECTOR_TYPE_CODES),
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key="ndw_connector_type",
            )
        ),
        vol.Optional(CONF_MIN_POWER_KW, default=settings.minimum_power_kw): vol.Any(
            None,
            vol.All(
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                _validate_minimum_power_kw,
            ),
        ),
        vol.Required(
            CONF_REQUIRE_AVAILABLE, default=settings.require_available
        ): selector.BooleanSelector(),
    }


def _validate_minimum_power_kw(value: Any) -> float:
    """Validate the positive, finite power selector value."""
    try:
        settings = DiscoveryFilterSettings.from_config({CONF_MIN_POWER_KW: value})
    except ValueError as err:
        raise vol.Invalid("Minimum power must be a finite positive number") from err
    assert settings.minimum_power_kw is not None
    return settings.minimum_power_kw


def options_schema(
    defaults: dict[str, Any],
    *,
    favourite_options: list[selector.SelectOptionDict] | None = None,
) -> vol.Schema:
    """Return the editable runtime options schema."""
    provider_id = defaults.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
    if provider_id not in (PROVIDER_NDW_DOT_NL, PROVIDER_ECO_MOVEMENT_BE):
        provider_id = PROVIDER_NDW_DOT_NL
    favourites = parse_favourite_references(
        defaults.get(CONF_FAVOURITES, []), provider_id
    )
    schema: dict[Any, Any] = {
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
        vol.Required(CONF_PROVIDER, default=provider_id): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[provider_id],
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="provider",
            )
        ),
        vol.Required(
            CONF_INCLUDE_UNKNOWN,
            default=defaults.get(CONF_INCLUDE_UNKNOWN, DEFAULT_INCLUDE_UNKNOWN),
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_FAVOURITES,
            default=[favourite.provider_id for favourite in favourites],
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=favourite_options or [],
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
    if provider_id == PROVIDER_NDW_DOT_NL:
        schema.update(
            _discovery_filter_fields(DiscoveryFilterSettings.from_config(defaults))
        )
    if defaults.get(CONF_LOCATION_SOURCE) == LOCATION_ENTITY:
        schema[
            vol.Required(
                CONF_LOCATION_MOVEMENT_THRESHOLD,
                default=defaults.get(
                    CONF_LOCATION_MOVEMENT_THRESHOLD,
                    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
                ),
            )
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )
    return vol.Schema(schema)


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
        self._location_entity_id: str | None = None
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._provider_id = PROVIDER_NDW_DOT_NL
        self._search_radius = DEFAULT_SEARCH_RADIUS_M
        self._max_stations = DEFAULT_MAX_STATIONS
        self._discovery_filters = DiscoveryFilterSettings()

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
            if self._location_source == LOCATION_ENTITY:
                return await self.async_step_entity_location()
            return await self.async_step_manual_location()

        return _show_form(self, step_id="user", data_schema=_location_source_schema())

    async def async_step_entity_location(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select an entity with current valid coordinates."""
        if user_input is None:
            return _show_form(
                self,
                step_id="entity_location",
                data_schema=entity_location_schema(),
            )

        entity_id = user_input[CONF_LOCATION_ENTITY]
        location = resolve_location(
            self.hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: entity_id,
            },
        )
        if location is None:
            return _show_form(
                self,
                step_id="entity_location",
                data_schema=entity_location_schema(entity_id),
                errors={"base": "invalid_entity_location"},
            )
        self._location_source = LOCATION_ENTITY
        self._location_entity_id = entity_id
        self._latitude, self._longitude = location
        return await self.async_step_station_options()

    async def async_step_reauth(
        self, _entry_data: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for a replacement Belgium API token."""
        provider_id = (
            _entry_data.get(CONF_PROVIDER)
            if _entry_data is not None
            else self._get_reauth_entry().data.get(CONF_PROVIDER)
        )
        if provider_id != PROVIDER_ECO_MOVEMENT_BE:
            return self.async_abort(reason="unsupported_provider")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate and save a replacement Belgium API token."""
        entry = self._get_reauth_entry()
        if user_input is None:
            return _show_form(
                self,
                step_id="reauth_confirm",
                data_schema=belgium_credentials_schema(),
            )

        token = user_input[CONF_API_TOKEN]
        if not is_valid_belgium_api_token(token):
            return _show_form(
                self,
                step_id="reauth_confirm",
                data_schema=belgium_credentials_schema(),
                errors={"base": "invalid_api_token"},
            )

        options = {**entry.data, **entry.options}
        provider_id = options.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
        location = _entry_location(self.hass, entry.data)
        if provider_id != PROVIDER_ECO_MOVEMENT_BE:
            return self.async_abort(reason="unsupported_provider")
        if location is None:
            return self.async_abort(reason="location_not_configured")

        try:
            provider = create_provider(
                provider_id, async_get_clientsession(self.hass), self.hass, token
            )
            await provider.async_validate_connection(
                location[0],
                location[1],
                int(options.get(CONF_SEARCH_RADIUS, DEFAULT_SEARCH_RADIUS_M)),
            )
        except ChargingProviderError as err:
            return _show_form(
                self,
                step_id="reauth_confirm",
                data_schema=belgium_credentials_schema(),
                errors={"base": _flow_error(err)},
            )

        data = dict(entry.data)
        data[CONF_API_TOKEN] = token
        return self.async_update_reload_and_abort(
            entry,
            data=data,
            reason="reauth_successful",
        )

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
        if self._location_source == LOCATION_ENTITY:
            return await self.async_step_reconfigure_entity_location()

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

    async def async_step_reconfigure_entity_location(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select or replace an Entity search location during reconfigure."""
        entry = self._get_reconfigure_entry()
        if user_input is None:
            default_entity_id = entry.data.get(CONF_LOCATION_ENTITY)
            return _show_form(
                self,
                step_id="reconfigure_entity_location",
                data_schema=entity_location_schema(default_entity_id),
            )

        entity_id = user_input[CONF_LOCATION_ENTITY]
        location = resolve_location(
            self.hass,
            {
                CONF_LOCATION_SOURCE: LOCATION_ENTITY,
                CONF_LOCATION_ENTITY: entity_id,
            },
        )
        if location is None:
            return _show_form(
                self,
                step_id="reconfigure_entity_location",
                data_schema=entity_location_schema(entity_id),
                errors={"base": "invalid_entity_location"},
            )
        self._location_source = LOCATION_ENTITY
        self._location_entity_id = entity_id
        self._latitude, self._longitude = location
        return await self._async_save_reconfigured_location(entry)

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
            provider = create_provider(
                provider_id,
                async_get_clientsession(self.hass),
                self.hass,
                entry.data.get(CONF_API_TOKEN),
            )
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
            if self._location_source == LOCATION_ENTITY:
                assert self._location_entity_id is not None
                return _show_form(
                    self,
                    step_id="reconfigure_entity_location",
                    data_schema=entity_location_schema(self._location_entity_id),
                    errors={"base": _flow_error(err)},
                )
            return _show_form(
                self,
                step_id="reconfigure",
                data_schema=_location_source_schema(LOCATION_HOME),
                errors={"base": _flow_error(err)},
            )

        if self._location_source == LOCATION_ENTITY:
            assert self._location_entity_id is not None
            new_unique_id = _config_unique_id_entity(
                provider_id, self._location_entity_id, radius
            )
        else:
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
        if self._location_source == LOCATION_ENTITY:
            assert self._location_entity_id is not None
            data[CONF_LOCATION_ENTITY] = self._location_entity_id
        else:
            data.pop(CONF_LOCATION_ENTITY, None)
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
            self._provider_id = provider_id
            self._search_radius = radius
            self._max_stations = max_stations
            if self._location_source == LOCATION_ENTITY:
                assert self._location_entity_id is not None
                unique_id = _config_unique_id_entity(
                    provider_id, self._location_entity_id, radius
                )
            else:
                unique_id = _config_unique_id(
                    provider_id, self._latitude, self._longitude, radius
                )
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            if provider_id == PROVIDER_ECO_MOVEMENT_BE:
                return await self.async_step_belgium_credentials()
            return await self.async_step_discovery_filters()

        return _show_form(
            self, step_id="station_options", data_schema=station_options_schema()
        )

    async def async_step_discovery_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure the optional NDW connector-group filters."""
        if user_input is not None:
            self._discovery_filters = DiscoveryFilterSettings.from_config(user_input)
            return await self._async_validate_and_create_entry()

        return _show_form(
            self,
            step_id="discovery_filters",
            data_schema=vol.Schema(_discovery_filter_fields(self._discovery_filters)),
        )

    async def async_step_belgium_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect and validate the Belgium API token."""
        if user_input is None:
            return _show_form(
                self,
                step_id="belgium_credentials",
                data_schema=belgium_credentials_schema(),
            )

        token = user_input[CONF_API_TOKEN]
        if not is_valid_belgium_api_token(token):
            return _show_form(
                self,
                step_id="belgium_credentials",
                data_schema=belgium_credentials_schema(),
                errors={"base": "invalid_api_token"},
            )
        return await self._async_validate_and_create_entry(token)

    async def _async_validate_and_create_entry(
        self, token: str | None = None
    ) -> FlowResult:
        """Validate the selected provider and create the config entry."""
        assert self._latitude is not None
        assert self._longitude is not None
        assert self._location_source is not None

        try:
            provider = create_provider(
                self._provider_id,
                async_get_clientsession(self.hass),
                self.hass,
                token,
            )
            await provider.async_validate_connection(
                self._latitude, self._longitude, self._search_radius
            )
        except ChargingProviderError as err:
            if self._provider_id == PROVIDER_ECO_MOVEMENT_BE:
                return _show_form(
                    self,
                    step_id="belgium_credentials",
                    data_schema=belgium_credentials_schema(),
                    errors={"base": _flow_error(err)},
                )
            return _show_form(
                self,
                step_id="discovery_filters",
                data_schema=vol.Schema(
                    _discovery_filter_fields(self._discovery_filters)
                ),
                errors={"base": _flow_error(err)},
            )

        data: dict[str, Any] = {
            CONF_LOCATION_SOURCE: self._location_source,
            CONF_PROVIDER: self._provider_id,
            CONF_SEARCH_RADIUS: self._search_radius,
            CONF_MAX_STATIONS: self._max_stations,
        }
        if self._location_source == LOCATION_MANUAL:
            data[CONF_LATITUDE] = self._latitude
            data[CONF_LONGITUDE] = self._longitude
        elif self._location_source == LOCATION_ENTITY:
            assert self._location_entity_id is not None
            data[CONF_LOCATION_ENTITY] = self._location_entity_id
        if self._provider_id == PROVIDER_NDW_DOT_NL:
            data.update(self._discovery_filters.as_config())
        if token is not None:
            data[CONF_API_TOKEN] = token
        return self.async_create_entry(title="CurbCharge", data=data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> CurbChargeOptionsFlow:
        """Create the options flow."""
        return CurbChargeOptionsFlow()


class CurbChargeOptionsFlow(config_entries.OptionsFlow):
    """Edit refresh, search, and favourite behavior."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Save options or show their current values."""
        defaults = {**self.config_entry.data, **self.config_entry.options}
        provider_id = defaults.get(CONF_PROVIDER, PROVIDER_NDW_DOT_NL)
        if provider_id not in (PROVIDER_NDW_DOT_NL, PROVIDER_ECO_MOVEMENT_BE):
            provider_id = PROVIDER_NDW_DOT_NL
        saved_favourites = parse_favourite_references(
            defaults.get(CONF_FAVOURITES, []), provider_id
        )
        favourite_options, favourite_references = _favourite_selector_options(
            getattr(self.config_entry, "runtime_data", None), saved_favourites
        )

        if user_input is not None:
            discovery_filters = (
                DiscoveryFilterSettings.from_config(user_input)
                if provider_id == PROVIDER_NDW_DOT_NL
                else None
            )
            try:
                favourites = _selected_favourites(
                    user_input.get(CONF_FAVOURITES),
                    favourite_references,
                    saved_favourites,
                    getattr(self.config_entry, "runtime_data", None),
                )
            except ValueError:
                return _show_form(
                    self,
                    step_id="init",
                    data_schema=options_schema(
                        defaults, favourite_options=favourite_options
                    ),
                    errors={"base": "invalid_favourites"},
                )
            data = {
                CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                CONF_SEARCH_RADIUS: int(user_input[CONF_SEARCH_RADIUS]),
                CONF_MAX_STATIONS: int(user_input[CONF_MAX_STATIONS]),
                CONF_PROVIDER: user_input[CONF_PROVIDER],
                CONF_INCLUDE_UNKNOWN: user_input[CONF_INCLUDE_UNKNOWN],
                CONF_FAVOURITES: [asdict(favourite) for favourite in favourites],
            }
            if discovery_filters is not None:
                data.update(discovery_filters.as_config())
            if defaults.get(CONF_LOCATION_SOURCE) == LOCATION_ENTITY:
                data[CONF_LOCATION_MOVEMENT_THRESHOLD] = int(
                    user_input.get(
                        CONF_LOCATION_MOVEMENT_THRESHOLD,
                        defaults.get(
                            CONF_LOCATION_MOVEMENT_THRESHOLD,
                            DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
                        ),
                    )
                )
            return self.async_create_entry(title="", data=data)

        return _show_form(
            self,
            step_id="init",
            data_schema=options_schema(defaults, favourite_options=favourite_options),
        )


def _favourite_selector_options(
    coordinator: CurbChargeCoordinator | None,
    saved_favourites: tuple[FavouriteStationReference, ...],
) -> tuple[list[selector.SelectOptionDict], dict[str, FavouriteStationReference]]:
    """Build human-readable options while retaining saved out-of-radius entries."""
    references = {favourite.provider_id: favourite for favourite in saved_favourites}
    labels = {favourite.provider_id: favourite.name for favourite in saved_favourites}
    if coordinator is not None:
        for station in (coordinator.data or {}).values():
            if station.is_stale:
                continue
            references[station.provider_id] = _favourite_reference(station)
            labels[station.provider_id] = _favourite_label(station)

    counts: dict[str, int] = {}
    for label in labels.values():
        counts[label] = counts.get(label, 0) + 1
    options: list[selector.SelectOptionDict] = [
        {
            "value": provider_id,
            "label": (f"{label} ({provider_id[-6:]})" if counts[label] > 1 else label),
        }
        for provider_id, label in sorted(
            labels.items(), key=lambda item: (item[1].casefold(), item[0])
        )
    ]
    return options, references


def _favourite_reference(station: ChargingStation) -> FavouriteStationReference:
    location = station.location
    location_id = None
    location_name = None
    location_operator = None
    if (
        location is not None
        and location.provider == station.provider
        and location.provider_id != station.provider_id
    ):
        location_id = location.provider_id
        location_name = location.name
        location_operator = location.operator
    return FavouriteStationReference(
        provider=station.provider,
        provider_id=station.provider_id,
        name=station.address or station.name,
        latitude=station.latitude,
        longitude=station.longitude,
        location_id=location_id,
        location_name=location_name,
        location_operator=location_operator,
    )


def _favourite_label(station: ChargingStation) -> str:
    """Describe a station by its address/name and distance, not its raw ID."""
    label = station.address or station.name
    if station.name != label:
        label = f"{label} - {station.name}"
    if station.operator:
        label = f"{label} - {station.operator}"
    if station.distance_m is not None:
        label = f"{label} ({station.distance_m} m)"
    return label


def _selected_favourites(
    selected_ids: object,
    available_references: dict[str, FavouriteStationReference],
    saved_favourites: tuple[FavouriteStationReference, ...],
    coordinator: CurbChargeCoordinator | None,
) -> tuple[FavouriteStationReference, ...]:
    """Resolve selector values and reject selections no longer present."""
    if not isinstance(selected_ids, list) or any(
        not isinstance(provider_id, str) for provider_id in selected_ids
    ):
        raise ValueError("Favourite selection must be a list of station IDs")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("Favourite selection contains duplicate station IDs")

    saved_ids = {favourite.provider_id for favourite in saved_favourites}
    current_ids = (
        {
            station.provider_id
            for station in (coordinator.data or {}).values()
            if not station.is_stale
        }
        if coordinator is not None
        else set()
    )
    favourites: list[FavouriteStationReference] = []
    for provider_id in selected_ids:
        favourite = available_references.get(provider_id)
        if favourite is None or (
            provider_id not in saved_ids and provider_id not in current_ids
        ):
            raise ValueError("Selected favourite is no longer available")
        favourites.append(favourite)
    return tuple(favourites)


def _location_source_schema(
    default: str = LOCATION_HOME,
) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_LOCATION_SOURCE, default=default
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[LOCATION_HOME, LOCATION_MANUAL, LOCATION_ENTITY],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="location_source",
                )
            )
        }
    )


def _home_location(hass: HomeAssistant) -> tuple[float, float] | None:
    return resolve_location(hass, {CONF_LOCATION_SOURCE: LOCATION_HOME})


def _entry_location(
    hass: HomeAssistant, data: Mapping[str, Any]
) -> tuple[float, float] | None:
    """Return the config entry's current home, manual, or entity location."""
    return resolve_location(hass, data)


def _valid_coordinates(latitude: float, longitude: float) -> bool:
    return valid_coordinates(latitude, longitude) is not None


def _config_unique_id(
    provider: str, latitude: float, longitude: float, radius: int
) -> str:
    area_digest = hashlib.sha256(
        f"{latitude:.6f},{longitude:.6f},{radius}".encode()
    ).hexdigest()[:16]
    return f"{provider}_{area_digest}"


def _config_unique_id_entity(provider: str, entity_id: str, radius: int) -> str:
    entity_digest = hashlib.sha256(f"entity:{entity_id}:{radius}".encode()).hexdigest()[
        :16
    ]
    return f"{provider}_entity_{entity_digest}"


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
