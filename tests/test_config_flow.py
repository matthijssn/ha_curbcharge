"""Tests for CurbCharge setup and options flows."""

from dataclasses import replace
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace

import pytest
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_validation as cv
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge import config_flow, schema
from custom_components.curbcharge.const import (
    CONF_ALLOW_FALLBACK,
    CONF_API_TOKEN,
    CONF_CONNECTOR_TYPE_IDS,
    CONF_CONNECTOR_TYPES,
    CONF_EXCLUDED_OPERATORS,
    CONF_FAVOURITES,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_MOVEMENT_THRESHOLD,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_MAXIMUM_PRICE,
    CONF_MAXIMUM_PRICE_CURRENCY,
    CONF_MIN_POWER_KW,
    CONF_PERSONALIZATION,
    CONF_PERSONALIZATION_ENABLED,
    CONF_PERSONALIZATION_MIN_POWER_KW,
    CONF_PERSONALIZATION_VERSION,
    CONF_PREFERRED_OPERATORS,
    CONF_PROVIDER,
    CONF_RANKING_PRESET,
    CONF_REQUIRE_AVAILABLE,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    CONF_UNKNOWN_DATA_POLICY,
    DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M,
    DEFAULT_SCAN_INTERVAL_MINUTES,
    DOMAIN,
    LOCATION_ENTITY,
    LOCATION_HOME,
    LOCATION_MANUAL,
    NDW_CONNECTOR_TYPE_CODES,
    PERSONALIZATION_PROFILE_VERSION,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.coordinator import CurbChargeCoordinator
from custom_components.curbcharge.models import (
    ConnectorTypeId,
    DiscoveryFilterSettings,
    FavouriteStationReference,
    PersonalizationProfile,
    PersonalizationRankingPreset,
    PersonalizationUnknownDataPolicy,
)
from custom_components.curbcharge.provider import (
    AuthenticationError,
    ChargingProviderError,
    InvalidProviderResponseError,
    ProviderConnectionError,
    ProviderRateLimitError,
    UnsupportedLocationError,
    UnsupportedProviderError,
)
from tests.conftest import FakeProvider

flow_schema = config_flow.vol
pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def test_flow_schema_module_falls_back_to_voluptuous(monkeypatch):
    voluptuous = import_module("voluptuous")
    imports = []

    def import_schema_module(name: str):
        imports.append(name)
        if name == "probatio":
            raise ModuleNotFoundError("No module named 'probatio'", name="probatio")
        return voluptuous

    monkeypatch.setattr(schema, "import_module", import_schema_module)

    assert schema.load_schema_module() is voluptuous
    assert imports == ["probatio", "voluptuous"]


def test_flow_schema_module_propagates_unrelated_import_errors(monkeypatch):
    def fail_import(name: str):
        assert name == "probatio"
        raise ModuleNotFoundError(
            "No module named 'broken_dependency'", name="broken_dependency"
        )

    monkeypatch.setattr(schema, "import_module", fail_import)

    with pytest.raises(ModuleNotFoundError, match="broken_dependency"):
        schema.load_schema_module()


def test_discovery_filter_schema_serializes_with_probatio():
    to_field_list = getattr(config_flow.vol, "to_field_list", None)
    if to_field_list is None:
        pytest.skip("Probatio field-list serialization is unavailable")

    fields = to_field_list(
        config_flow.vol.Schema(config_flow._discovery_filter_fields()),
        custom_serializer=cv.custom_serializer,
    )

    assert fields


def test_probatio_filter_schema_uses_serializable_range(monkeypatch):
    voluptuous = import_module("voluptuous")
    probatio = SimpleNamespace(
        __name__="probatio",
        All=voluptuous.All,
        Any=voluptuous.Any,
        Optional=voluptuous.Optional,
        Range=voluptuous.Range,
        Required=voluptuous.Required,
    )
    monkeypatch.setattr(config_flow, "vol", probatio)
    monkeypatch.setattr(
        config_flow,
        "_validate_minimum_power_kw",
        lambda _value: pytest.fail("Probatio schemas must not contain callables"),
    )

    fields = config_flow._discovery_filter_fields()
    minimum_power_key = next(
        key for key in fields if getattr(key, "schema", None) == CONF_MIN_POWER_KW
    )
    schema = voluptuous.Schema({minimum_power_key: fields[minimum_power_key]})

    assert schema({CONF_MIN_POWER_KW: 7.2})[CONF_MIN_POWER_KW] == 7.2
    with pytest.raises(voluptuous.Invalid):
        schema({CONF_MIN_POWER_KW: 0})


def _station_options(
    provider: str = PROVIDER_NDW_DOT_NL,
    *,
    scan_interval: int = DEFAULT_SCAN_INTERVAL_MINUTES,
) -> dict[str, int | str]:
    return {
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_SCAN_INTERVAL: scan_interval,
        CONF_PROVIDER: provider,
    }


def test_station_options_schema_reuses_scan_interval_bounds():
    schema = config_flow.station_options_schema()
    values = {
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
    }

    assert schema(values)[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL_MINUTES
    assert schema({**values, CONF_SCAN_INTERVAL: 1})[CONF_SCAN_INTERVAL] == 1
    assert schema({**values, CONF_SCAN_INTERVAL: 120})[CONF_SCAN_INTERVAL] == 120
    with pytest.raises(flow_schema.Invalid):
        schema({**values, CONF_SCAN_INTERVAL: 0})
    assert (
        config_flow.options_schema({CONF_SCAN_INTERVAL: 12})({})[CONF_SCAN_INTERVAL]
        == 12
    )


def _discovery_filters(
    connector_types: list[str] | None = None,
    minimum_power_kw: float | None = None,
    require_available: bool = False,
) -> dict[str, object]:
    selector_value_by_code = {
        code: selector_value
        for selector_value, code in config_flow.NDW_CONNECTOR_TYPE_SELECTOR_OPTIONS
    }
    return {
        CONF_CONNECTOR_TYPES: [
            selector_value_by_code.get(connector_type, connector_type)
            for connector_type in connector_types or []
        ],
        CONF_MIN_POWER_KW: minimum_power_kw,
        CONF_REQUIRE_AVAILABLE: require_available,
    }


async def _open_options_step(hass, entry, step_id: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"general", "personalization"}
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step_id}
    )


@pytest.mark.asyncio
async def test_config_flow_uses_home_coordinates(hass, monkeypatch):
    hass.config.latitude = 52.1
    hass.config.longitude = 5.1
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    monkeypatch.setattr(
        "custom_components.curbcharge.create_provider", lambda *_args: provider
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    assert result["step_id"] == "station_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options(scan_interval=12)
    )
    assert result["step_id"] == "discovery_filters"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _discovery_filters()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert provider.validation_calls == [(52.1, 5.1, 500)]
    assert result["data"][CONF_LOCATION_SOURCE] == LOCATION_HOME
    assert result["data"][CONF_SCAN_INTERVAL] == 12
    assert result["data"][CONF_CONNECTOR_TYPES] == []
    assert result["data"][CONF_MIN_POWER_KW] is None
    assert result["data"][CONF_REQUIRE_AVAILABLE] is False
    assert "latitude" not in result["data"]
    assert "longitude" not in result["data"]
    assert "52.1" not in result["result"].unique_id


@pytest.mark.asyncio
async def test_discovery_filter_flow_shows_error_for_non_finite_power(hass):
    hass.config.latitude = 52.1
    hass.config.longitude = 5.1
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options()
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]

    result = await flow.async_step_discovery_filters(
        _discovery_filters(minimum_power_kw=float("nan"))
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_filters"
    assert result["errors"] == {"base": "invalid_discovery_filters"}


@pytest.mark.asyncio
async def test_config_flow_uses_manual_coordinates(hass, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"latitude": 52.123456, "longitude": 5.654321}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options()
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _discovery_filters(["IEC_62196_T2"], 7.2, True)
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert provider.validation_calls == [(52.123456, 5.654321, 500)]
    assert result["data"]["latitude"] == 52.123456
    assert result["data"]["longitude"] == 5.654321
    assert result["data"][CONF_CONNECTOR_TYPES] == ["IEC_62196_T2"]
    assert result["data"][CONF_MIN_POWER_KW] == 7.2
    assert result["data"][CONF_REQUIRE_AVAILABLE] is True
    assert "52.123456" not in result["result"].unique_id
    assert "5.654321" not in result["result"].unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_id", "state", "extra_attributes"),
    [
        ("sensor.vehicle_location", "moving", {}),
        (
            "zone.home",
            "0",
            {
                "radius": 100,
                "passive": False,
                "persons": [],
                "device_trackers": [],
                "editable": True,
            },
        ),
    ],
)
async def test_config_flow_uses_entity_location_without_storing_coordinates(
    hass, monkeypatch, entity_id, state, extra_attributes
):
    location = (52.123456, 5.654321)
    hass.states.async_set(
        entity_id,
        state,
        {
            CONF_LATITUDE: location[0],
            CONF_LONGITUDE: location[1],
            **extra_attributes,
        },
    )
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    assert result["step_id"] == "entity_location"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )
    assert result["step_id"] == "station_options"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options()
    )
    assert result["step_id"] == "discovery_filters"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _discovery_filters()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert provider.validation_calls == [(*location, 500)]
    assert result["data"][CONF_LOCATION_SOURCE] == LOCATION_ENTITY
    assert result["data"][CONF_LOCATION_ENTITY] == entity_id
    assert CONF_LATITUDE not in result["data"]
    assert CONF_LONGITUDE not in result["data"]
    assert result["result"].unique_id == config_flow._config_unique_id_entity(
        PROVIDER_NDW_DOT_NL, entity_id, 500
    )
    assert entity_id not in result["result"].unique_id


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["unknown", "unavailable"])
async def test_config_flow_rejects_entity_without_current_coordinates(
    hass, monkeypatch, state
):
    entity_id = "sensor.vehicle_location"
    hass.states.async_set(
        entity_id,
        state,
        {CONF_LATITUDE: 52.1, CONF_LONGITUDE: 5.1},
    )
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "entity_location"
    assert result["errors"] == {"base": "invalid_entity_location"}
    assert provider.validation_calls == []


@pytest.mark.asyncio
async def test_reconfigure_legacy_netherlands_entry_defaults_to_ndw(hass, monkeypatch):
    hass.config.latitude = 52.3
    hass.config.longitude = 5.3
    provider = FakeProvider()
    calls: list[tuple[object, ...]] = []

    def create_provider(*args):
        calls.append(args)
        return provider

    monkeypatch.setattr(config_flow, "create_provider", create_provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="legacy-dutch-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert calls[0][0] == PROVIDER_NDW_DOT_NL
    assert calls[0][2:] == (hass, None)
    assert provider.validation_calls == [(52.3, 5.3, 500)]
    assert entry.unique_id == config_flow._config_unique_id(
        PROVIDER_NDW_DOT_NL, 52.3, 5.3, 500
    )
    assert CONF_PROVIDER not in entry.data


@pytest.mark.asyncio
async def test_belgium_config_flow_validates_and_stores_token(hass, monkeypatch):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35
    provider = FakeProvider()
    calls: list[tuple[object, ...]] = []

    def create_provider(*args):
        calls.append(args)
        return provider

    monkeypatch.setattr(config_flow, "create_provider", create_provider)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options(PROVIDER_ECO_MOVEMENT_BE)
    )
    assert result["step_id"] == "belgium_credentials"

    token = "test-belgium-token"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: token}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PROVIDER] == PROVIDER_ECO_MOVEMENT_BE
    assert result["data"][CONF_API_TOKEN] == token
    assert CONF_API_TOKEN not in result["result"].options
    assert provider.validation_calls == [(50.85, 4.35, 500)]
    assert calls[0][0] == PROVIDER_ECO_MOVEMENT_BE
    assert calls[0][2:] == (hass, token)


@pytest.mark.asyncio
async def test_belgium_config_flow_rejects_malformed_token_before_network(
    hass, monkeypatch
):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35

    def fail_provider(*_args):
        raise AssertionError("Invalid tokens must not reach the provider")

    monkeypatch.setattr(config_flow, "create_provider", fail_provider)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options(PROVIDER_ECO_MOVEMENT_BE)
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "bad\nheader"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "belgium_credentials"
    assert result["errors"]["base"] == "invalid_api_token"


@pytest.mark.asyncio
async def test_belgium_config_flow_keeps_token_form_on_auth_failure(hass, monkeypatch):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35
    provider = FakeProvider()
    provider.validation_error = AuthenticationError("private token details")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options(PROVIDER_ECO_MOVEMENT_BE)
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "test-belgium-token"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "belgium_credentials"
    assert result["errors"]["base"] == "auth"


@pytest.mark.asyncio
async def test_reauth_replaces_belgium_token_and_preserves_unique_id(hass, monkeypatch):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    location_id = config_flow._config_unique_id(
        PROVIDER_ECO_MOVEMENT_BE, 50.85, 4.35, 500
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=location_id,
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_API_TOKEN: "old-token",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "replacement-token"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_TOKEN] == "replacement-token"
    assert entry.unique_id == location_id
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert provider.validation_calls == [(50.85, 4.35, 500)]


@pytest.mark.asyncio
async def test_cancelled_reauth_keeps_existing_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="belgium-search-area",
        data={
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: "old-token",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    hass.config_entries.flow.async_abort(result["flow_id"])

    assert entry.data[CONF_API_TOKEN] == "old-token"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


@pytest.mark.asyncio
async def test_reauth_keeps_form_open_when_provider_rejects_replacement(
    hass, monkeypatch
):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35
    provider = FakeProvider()
    provider.validation_error = AuthenticationError("private token details")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="belgium-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_API_TOKEN: "old-token",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "invalid-token"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "auth"
    assert entry.data[CONF_API_TOKEN] == "old-token"


@pytest.mark.asyncio
async def test_reauth_aborts_for_a_provider_without_credentials(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="dutch-search-area",
        data={CONF_PROVIDER: PROVIDER_NDW_DOT_NL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_provider"


@pytest.mark.asyncio
async def test_reauth_rejects_a_provider_changed_in_options(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="belgium-search-area",
        data={CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE},
        options={CONF_PROVIDER: PROVIDER_NDW_DOT_NL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "replacement-token"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_provider"


@pytest.mark.asyncio
async def test_reauth_requires_the_configured_location(hass):
    hass.config.latitude = 0
    hass.config.longitude = 0
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="belgium-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: "old-token",
            CONF_SEARCH_RADIUS: 500,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "replacement-token"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_not_configured"


@pytest.mark.asyncio
async def test_reauth_rejects_a_malformed_token_before_provider_validation(
    hass, monkeypatch
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="belgium-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_API_TOKEN: "old-token",
            CONF_SEARCH_RADIUS: 500,
        },
    )
    entry.add_to_hass(hass)

    def fail_provider(*_args):
        raise AssertionError("Invalid tokens must not reach the provider")

    monkeypatch.setattr(config_flow, "create_provider", fail_provider)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_TOKEN: "bad\nheader"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "invalid_api_token"


@pytest.mark.asyncio
async def test_belgium_reconfigure_reuses_saved_token(hass, monkeypatch):
    hass.config.latitude = 52.3
    hass.config.longitude = 5.3
    old_location = (52.1, 5.1)
    provider = FakeProvider()
    calls: list[tuple[object, ...]] = []

    def create_provider(*args):
        calls.append(args)
        return provider

    monkeypatch.setattr(config_flow, "create_provider", create_provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_ECO_MOVEMENT_BE, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_API_TOKEN: "saved-token",
            "latitude": old_location[0],
            "longitude": old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert calls[0][0] == PROVIDER_ECO_MOVEMENT_BE
    assert calls[0][2:] == (hass, "saved-token")
    assert entry.data[CONF_API_TOKEN] == "saved-token"


@pytest.mark.asyncio
async def test_belgium_duplicate_search_area_aborts_before_asking_for_token(hass):
    hass.config.latitude = 50.85
    hass.config.longitude = 4.35
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_ECO_MOVEMENT_BE, 50.85, 4.35, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_API_TOKEN: "existing-token",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options(PROVIDER_ECO_MOVEMENT_BE)
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def test_manual_coordinates_and_radius_are_range_validated():
    with pytest.raises(flow_schema.Invalid):
        config_flow.manual_location_schema()({"latitude": 91, "longitude": 5})
    with pytest.raises(flow_schema.Invalid):
        config_flow.manual_location_schema()({"latitude": 52, "longitude": 181})
    with pytest.raises(flow_schema.Invalid):
        config_flow.station_options_schema()(
            {
                CONF_SEARCH_RADIUS: 10_001,
                CONF_MAX_STATIONS: 10,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            }
        )
    assert (
        config_flow.options_schema({CONF_PROVIDER: "unsupported"})(
            {
                CONF_SCAN_INTERVAL: 5,
                CONF_SEARCH_RADIUS: 500,
                CONF_MAX_STATIONS: 10,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
                CONF_INCLUDE_UNKNOWN: True,
            }
        )[CONF_PROVIDER]
        == PROVIDER_NDW_DOT_NL
    )
    with pytest.raises(flow_schema.Invalid):
        config_flow.station_options_schema()(
            {
                CONF_SEARCH_RADIUS: 500,
                CONF_MAX_STATIONS: 51,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            }
        )


def test_discovery_filter_settings_default_and_validate():
    assert DiscoveryFilterSettings.from_config({}) == DiscoveryFilterSettings()
    settings = DiscoveryFilterSettings.from_config(
        _discovery_filters(
            ["IEC_62196_T2", "CHADEMO"],
            minimum_power_kw=11.0,
            require_available=True,
        )
    )
    assert settings.connector_types == ("IEC_62196_T2", "CHADEMO")
    assert settings.minimum_power_kw == 11.0
    assert settings.require_available

    saved_settings = DiscoveryFilterSettings.from_config(
        {
            CONF_CONNECTOR_TYPES: ["IEC_62196_T2", "IEC_62196_T2"],
            CONF_MIN_POWER_KW: 7,
        }
    )
    assert saved_settings.connector_types == ("IEC_62196_T2",)
    assert saved_settings.as_config() == {
        CONF_CONNECTOR_TYPES: ["IEC_62196_T2"],
        CONF_MIN_POWER_KW: 7.0,
        CONF_REQUIRE_AVAILABLE: False,
    }
    assert (
        DiscoveryFilterSettings.from_config({CONF_CONNECTOR_TYPES: None})
        == DiscoveryFilterSettings()
    )

    invalid_settings = (
        {CONF_CONNECTOR_TYPES: "IEC_62196_T2"},
        {CONF_CONNECTOR_TYPES: [1]},
        {CONF_CONNECTOR_TYPES: ["UNKNOWN_CODE"]},
        {CONF_MIN_POWER_KW: 0},
        {CONF_MIN_POWER_KW: -1},
        {CONF_MIN_POWER_KW: float("inf")},
        {CONF_MIN_POWER_KW: float("nan")},
        {CONF_MIN_POWER_KW: True},
        {CONF_REQUIRE_AVAILABLE: "true"},
    )
    for invalid in invalid_settings:
        with pytest.raises(ValueError):
            DiscoveryFilterSettings.from_config(invalid)


def test_ndw_discovery_filter_options_default_and_validate():
    schema = config_flow.options_schema({})
    values = {
        CONF_SCAN_INTERVAL: 5,
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: True,
    }
    defaults = schema(values)
    assert defaults[CONF_CONNECTOR_TYPES] == []
    assert defaults[CONF_MIN_POWER_KW] is None
    assert defaults[CONF_REQUIRE_AVAILABLE] is False

    restored_schema = config_flow.options_schema(
        {
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            **_discovery_filters(["IEC_62196_T2"], 7.2, True),
        }
    )
    restored = restored_schema(values)
    assert restored[CONF_CONNECTOR_TYPES] == ["type-2"]
    assert restored[CONF_MIN_POWER_KW] == 7.2
    assert restored[CONF_REQUIRE_AVAILABLE] is True

    selected = schema(
        {
            **values,
            **_discovery_filters(["IEC_62196_T2"], 7.2, True),
        }
    )
    assert selected[CONF_CONNECTOR_TYPES] == ["type-2"]
    assert selected[CONF_MIN_POWER_KW] == 7.2
    assert selected[CONF_REQUIRE_AVAILABLE] is True

    assert set(NDW_CONNECTOR_TYPE_CODES) >= {
        "IEC_62196_T2",
        "CHADEMO",
        "DOMESTIC_A",
    }
    invalid_power_values = (0, -1)
    if config_flow.vol.__name__ == "voluptuous":
        invalid_power_values += (float("inf"), float("nan"))
    for invalid_power in invalid_power_values:
        with pytest.raises(flow_schema.Invalid):
            schema({**values, **_discovery_filters(minimum_power_kw=invalid_power)})
    with pytest.raises(flow_schema.Invalid):
        schema({**values, **_discovery_filters(["UNKNOWN_CODE"])})

    belgium_schema = config_flow.options_schema(
        {CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE}
    )
    belgium_values = belgium_schema({**values, CONF_PROVIDER: PROVIDER_ECO_MOVEMENT_BE})
    assert CONF_CONNECTOR_TYPES not in belgium_values
    with pytest.raises(flow_schema.Invalid):
        belgium_schema({**values, CONF_PROVIDER: PROVIDER_NDW_DOT_NL})


def test_minimum_power_validator_rejects_non_finite_values():
    assert config_flow._validate_minimum_power_kw(7.2) == 7.2
    for invalid_power in (0, -1, float("inf"), float("nan"), True):
        with pytest.raises(flow_schema.Invalid):
            config_flow._validate_minimum_power_kw(invalid_power)


def test_options_allow_one_minute_and_reject_zero():
    schema = config_flow.options_schema({})
    valid = {
        CONF_SCAN_INTERVAL: 1,
        CONF_SEARCH_RADIUS: 100,
        CONF_MAX_STATIONS: 1,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: True,
    }

    assert schema(valid)[CONF_SCAN_INTERVAL] == 1
    assert schema(valid)[CONF_FAVOURITES] == []
    with pytest.raises(flow_schema.Invalid):
        schema({**valid, CONF_SCAN_INTERVAL: 0})


def test_entity_movement_threshold_defaults_and_is_entity_only():
    options = {
        CONF_SCAN_INTERVAL: 5,
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: True,
        CONF_FAVOURITES: [],
    }
    home_schema = config_flow.options_schema({})
    assert CONF_LOCATION_MOVEMENT_THRESHOLD not in home_schema(options)

    entity_schema = config_flow.options_schema({CONF_LOCATION_SOURCE: LOCATION_ENTITY})
    assert entity_schema(options)[CONF_LOCATION_MOVEMENT_THRESHOLD] == (
        DEFAULT_LOCATION_MOVEMENT_THRESHOLD_M
    )
    assert (
        entity_schema({**options, CONF_LOCATION_MOVEMENT_THRESHOLD: 0})[
            CONF_LOCATION_MOVEMENT_THRESHOLD
        ]
        == 0
    )
    with pytest.raises(flow_schema.Invalid):
        entity_schema({**options, CONF_LOCATION_MOVEMENT_THRESHOLD: -1})


def test_entry_location_validates_manual_coordinates(hass):
    assert config_flow._entry_location(hass, {}) is None
    assert (
        config_flow._entry_location(hass, {CONF_LATITUDE: True, CONF_LONGITUDE: 5})
        is None
    )
    assert (
        config_flow._entry_location(hass, {CONF_LATITUDE: 91, CONF_LONGITUDE: 5})
        is None
    )
    assert config_flow._entry_location(
        hass, {CONF_LATITUDE: 52.1, CONF_LONGITUDE: 5.1}
    ) == (52.1, 5.1)


@pytest.mark.asyncio
async def test_options_flow_menu_keeps_general_options_separate(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="options-menu",
        data=_station_options(),
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"general", "personalization"}
    general = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "general"}
    )
    assert general["type"] is FlowResultType.FORM
    assert general["step_id"] == "general"


@pytest.mark.asyncio
async def test_options_flow_saves_versioned_personalization_profile(hass):
    original_options = {
        CONF_SCAN_INTERVAL: 5,
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: True,
        CONF_FAVOURITES: [],
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="personalization-profile",
        data=_station_options(),
        options=original_options,
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "personalization")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "personalization"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PERSONALIZATION_ENABLED: True,
            CONF_CONNECTOR_TYPE_IDS: [
                ConnectorTypeId.TYPE_2.value.lower(),
                ConnectorTypeId.TYPE_2.value.lower(),
            ],
            CONF_PERSONALIZATION_MIN_POWER_KW: 11.0,
            CONF_PREFERRED_OPERATORS: (
                "ndw_dot_nl:NLX\n"
                "eco_movement_be:Example   CPO\n"
                "eco_movement_be:example cpo"
            ),
            CONF_EXCLUDED_OPERATORS: "ndw_dot_nl:OTHER",
            CONF_MAXIMUM_PRICE: "0.1200",
            CONF_MAXIMUM_PRICE_CURRENCY: "eur",
            CONF_RANKING_PRESET: PersonalizationRankingPreset.SHORTEST_DISTANCE.value,
            CONF_ALLOW_FALLBACK: False,
            CONF_UNKNOWN_DATA_POLICY: (
                PersonalizationUnknownDataPolicy.ALLOW_FALLBACK.value
            ),
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == original_options[CONF_SCAN_INTERVAL]
    assert entry.options[CONF_FAVOURITES] == original_options[CONF_FAVOURITES]
    profile = PersonalizationProfile.from_config(entry.options[CONF_PERSONALIZATION])
    assert profile.enabled
    assert profile.connector_type_ids == (ConnectorTypeId.TYPE_2,)
    assert profile.minimum_power_kw == 11
    assert profile.preferred_operators[1].key == "example cpo"
    assert len(profile.preferred_operators) == 2
    assert profile.maximum_price == Decimal("0.1200")
    assert profile.maximum_price_currency == "EUR"
    assert profile.as_config()[CONF_MAXIMUM_PRICE] == "0.1200"
    assert entry.options[CONF_PERSONALIZATION][CONF_PERSONALIZATION_VERSION] == (
        PERSONALIZATION_PROFILE_VERSION
    )


def test_personalization_form_conversion_defaults_and_rejects_bad_inputs():
    profile = config_flow._personalization_profile_from_input({})
    assert not profile.enabled
    assert profile.connector_type_ids == ()

    for connector_type_ids in ("not-a-list", [None], ["unknown"]):
        with pytest.raises(ValueError):
            config_flow._personalization_profile_from_input(
                {CONF_CONNECTOR_TYPE_IDS: connector_type_ids}
            )

    with pytest.raises(ValueError, match="decimal string"):
        config_flow._personalization_profile_from_input({CONF_MAXIMUM_PRICE: 1})
    with pytest.raises(ValueError, match="currency"):
        config_flow._personalization_profile_from_input(
            {CONF_MAXIMUM_PRICE_CURRENCY: 1}
        )


@pytest.mark.asyncio
async def test_general_options_preserve_a_saved_personalization_profile(hass):
    profile = PersonalizationProfile(
        enabled=True,
        connector_type_ids=(ConnectorTypeId.TYPE_2,),
    ).as_config()
    original_options = {
        CONF_SCAN_INTERVAL: 5,
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: True,
        CONF_FAVOURITES: [],
        CONF_PERSONALIZATION: profile,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="preserve-personalization",
        data=_station_options(),
        options=original_options,
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "general")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: True,
            CONF_FAVOURITES: [],
            **_discovery_filters(),
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PERSONALIZATION] == profile


@pytest.mark.asyncio
async def test_options_flow_surfaces_malformed_saved_personalization(hass):
    profile = PersonalizationProfile().as_config()
    profile[CONF_PERSONALIZATION_VERSION] = PERSONALIZATION_PROFILE_VERSION + 1
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="malformed-personalization",
        data=_station_options(),
        options={CONF_PERSONALIZATION: profile},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "personalization"
    assert result["errors"] == {"base": "invalid_saved_personalization_profile"}


@pytest.mark.asyncio
async def test_options_flow_reports_invalid_personalization_input(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="invalid-personalization",
        data=_station_options(),
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "personalization")

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PERSONALIZATION_ENABLED: True,
            CONF_CONNECTOR_TYPE_IDS: [],
            CONF_PERSONALIZATION_MIN_POWER_KW: None,
            CONF_PREFERRED_OPERATORS: "ndw_dot_nl:Same",
            CONF_EXCLUDED_OPERATORS: "ndw_dot_nl:Same",
            CONF_MAXIMUM_PRICE: "1.00",
            CONF_MAXIMUM_PRICE_CURRENCY: "EUR",
            CONF_RANKING_PRESET: PersonalizationRankingPreset.BALANCED.value,
            CONF_ALLOW_FALLBACK: True,
            CONF_UNKNOWN_DATA_POLICY: (PersonalizationUnknownDataPolicy.EXCLUDE.value),
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "personalization"
    assert result["errors"] == {"base": "invalid_personalization_profile"}


@pytest.mark.asyncio
async def test_options_flow_saves_integer_intervals_and_search_limits(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="existing-area",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "general")
    assert result["type"] is FlowResultType.FORM
    assert isinstance(result["data_schema"], flow_schema.Schema)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 1.0,
            CONF_SEARCH_RADIUS: 100.0,
            CONF_MAX_STATIONS: 5.0,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: False,
            **_discovery_filters(["IEC_62196_T2"], 11.0, True),
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCAN_INTERVAL: 1,
        CONF_SEARCH_RADIUS: 100,
        CONF_MAX_STATIONS: 5,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: False,
        CONF_CONNECTOR_TYPES: ["IEC_62196_T2"],
        CONF_MIN_POWER_KW: 11.0,
        CONF_REQUIRE_AVAILABLE: True,
        CONF_FAVOURITES: [],
    }


@pytest.mark.asyncio
async def test_options_flow_keeps_form_open_for_non_finite_power(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="invalid-filter-area",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.add_to_hass(hass)
    result = await _open_options_step(hass, entry, "general")
    flow = hass.config_entries.options._progress[result["flow_id"]]

    result = await flow.async_step_general(
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: True,
            CONF_FAVOURITES: [],
            **_discovery_filters(minimum_power_kw=float("inf")),
        }
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "general"
    assert result["errors"] == {"base": "invalid_discovery_filters"}


@pytest.mark.asyncio
async def test_options_flow_saves_entity_movement_threshold(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="entity-location",
        data={
            CONF_LOCATION_SOURCE: LOCATION_ENTITY,
            CONF_LOCATION_ENTITY: "sensor.vehicle_location",
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "general")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: True,
            CONF_FAVOURITES: [],
            CONF_LOCATION_MOVEMENT_THRESHOLD: 0,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LOCATION_MOVEMENT_THRESHOLD] == 0


@pytest.mark.asyncio
async def test_options_flow_saves_stable_favourite_identity(hass, sample_stations):
    station = sample_stations[0]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="favorite-area",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.add_to_hass(hass)
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entry.runtime_data = coordinator
    options, _references = config_flow._favourite_selector_options(coordinator, ())
    assert options[0]["value"] == station.provider_id
    assert station.address in options[0]["label"]

    result = await _open_options_step(hass, entry, "general")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: True,
            CONF_FAVOURITES: [station.provider_id],
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_FAVOURITES] == [
        {
            "provider": station.provider,
            "provider_id": station.provider_id,
            "name": station.provider_id,
            "latitude": station.latitude,
            "longitude": station.longitude,
            "location_id": None,
        }
    ]
    assert station.address not in str(entry.options[CONF_FAVOURITES])


def test_favourite_options_use_readable_disambiguated_labels_and_skip_stale(
    hass, sample_stations
):
    first = replace(
        sample_stations[0],
        provider_id="station-one",
        name="EVSE",
        address="Main Street",
        operator="Operator",
    )
    second = replace(first, provider_id="station-two")
    stale = replace(first, provider_id="stale-station", is_stale=True)
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data(
        {
            first.provider_id: first,
            second.provider_id: second,
            stale.provider_id: stale,
        }
    )

    options, references = config_flow._favourite_selector_options(coordinator, ())

    assert [option["value"] for option in options] == ["station-one", "station-two"]
    assert all("Main Street - EVSE - Operator" in item["label"] for item in options)
    assert first.provider_id[-6:] in options[0]["label"]
    assert second.provider_id[-6:] in options[1]["label"]
    assert "stale-station" not in references


def test_favourite_reference_keeps_belgium_parent_identity(sample_stations):
    station = sample_stations[0]
    assert station.location is not None
    site = replace(
        station.location,
        provider_id="site-1",
        name="Charging site",
        operator="Site operator",
    )

    favourite = config_flow._favourite_reference(replace(station, location=site))

    assert favourite.location_id == "site-1"
    assert favourite.name == favourite.provider_id
    assert "location_name" not in favourite.as_config()
    assert "location_operator" not in favourite.as_config()
    assert config_flow._favourite_reference(station).location_id is None


@pytest.mark.parametrize(
    "selection",
    (None, "station-one", [1], ["station-one", "station-one"], ["missing"]),
)
def test_selected_favourites_rejects_invalid_or_missing_values(selection):
    favourite = FavouriteStationReference(
        PROVIDER_NDW_DOT_NL, "station-one", "Station", 52.0, 5.0
    )

    with pytest.raises(ValueError):
        config_flow._selected_favourites(
            selection, {"station-one": favourite}, (), None
        )


def test_selected_saved_favourite_remains_selectable_without_live_data():
    favourite = FavouriteStationReference(
        PROVIDER_NDW_DOT_NL, "station-one", "Station", 52.0, 5.0
    )

    assert config_flow._selected_favourites(
        ["station-one"], {"station-one": favourite}, (favourite,), None
    ) == (favourite,)


@pytest.mark.asyncio
async def test_options_flow_shows_error_for_stale_new_favourite(hass, sample_stations):
    station = sample_stations[0]
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="invalid-favourite-area",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        },
    )
    entry.add_to_hass(hass)
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True
    )
    coordinator.async_set_updated_data({station.provider_id: station})
    entry.runtime_data = coordinator
    result = await _open_options_step(hass, entry, "general")

    coordinator.async_set_updated_data({})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 5,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: True,
            CONF_FAVOURITES: [station.provider_id],
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_favourites"}


@pytest.mark.asyncio
async def test_options_flow_defaults_unknown_provider_to_ndw(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="unknown-provider",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: "not_a_provider",
        },
    )
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "general")

    assert result["type"] is FlowResultType.FORM
    assert (
        result["data_schema"](
            {
                CONF_SCAN_INTERVAL: 5,
                CONF_SEARCH_RADIUS: 500,
                CONF_MAX_STATIONS: 10,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
                CONF_INCLUDE_UNKNOWN: True,
                CONF_FAVOURITES: [],
            }
        )[CONF_PROVIDER]
        == PROVIDER_NDW_DOT_NL
    )


@pytest.mark.asyncio
async def test_options_flow_falls_back_to_ndw_for_an_unknown_saved_provider(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="unknown-provider",
        data={
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_PROVIDER: "not_a_provider",
        },
    )
    entry.add_to_hass(hass)

    result = await _open_options_step(hass, entry, "general")

    assert result["type"] is FlowResultType.FORM
    assert (
        result["data_schema"](
            {
                CONF_SCAN_INTERVAL: 5,
                CONF_SEARCH_RADIUS: 500,
                CONF_MAX_STATIONS: 10,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
                CONF_INCLUDE_UNKNOWN: True,
                CONF_FAVOURITES: [],
            }
        )[CONF_PROVIDER]
        == PROVIDER_NDW_DOT_NL
    )


def test_stale_favourite_remains_selectable_for_removal(hass):
    favourite = FavouriteStationReference(
        provider=PROVIDER_NDW_DOT_NL,
        provider_id="remote-station",
        name="remote-station",
        latitude=52.2,
        longitude=5.2,
    )
    coordinator = CurbChargeCoordinator(
        hass, FakeProvider(), 52.0, 5.0, 500, 10, 5, True, (favourite,)
    )

    options, references = config_flow._favourite_selector_options(
        coordinator, (favourite,)
    )

    assert options == [{"value": "remote-station", "label": "remote-station"}]
    assert (
        config_flow._selected_favourites([], references, (favourite,), coordinator)
        == ()
    )
    assert config_flow._selected_favourites(
        ["remote-station"], references, (favourite,), coordinator
    ) == (favourite,)


@pytest.mark.asyncio
async def test_config_flow_surfaces_authentication_failure(hass, monkeypatch):
    provider = FakeProvider()
    provider.validation_error = AuthenticationError("not logged")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options()
    )
    assert result["step_id"] == "discovery_filters"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        _discovery_filters(["IEC_62196_T2"], 11.0, True),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "discovery_filters"
    assert result["errors"]["base"] == "auth"
    restored = result["data_schema"]({})
    assert restored[CONF_CONNECTOR_TYPES] == ["type-2"]
    assert restored[CONF_MIN_POWER_KW] == 11.0
    assert restored[CONF_REQUIRE_AVAILABLE] is True


@pytest.mark.asyncio
async def test_reconfigure_updates_manual_location_and_validates_connection(
    hass, monkeypatch
):
    old_location = (52.1, 5.1)
    new_location = (52.2, 5.2)
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": old_location[0],
            "longitude": old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"latitude": new_location[0], "longitude": new_location[1]},
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_LOCATION_SOURCE] == LOCATION_MANUAL
    assert entry.data["latitude"] == new_location[0]
    assert entry.data["longitude"] == new_location[1]
    assert entry.unique_id == config_flow._config_unique_id(
        PROVIDER_NDW_DOT_NL, *new_location, 500
    )
    assert provider.validation_calls == [(*new_location, 500)]


@pytest.mark.asyncio
async def test_reconfigure_can_switch_from_manual_to_entity_location(hass, monkeypatch):
    entity_id = "sensor.vehicle_location"
    location = (52.2, 5.2)
    hass.states.async_set(
        entity_id,
        "moving",
        {CONF_LATITUDE: location[0], CONF_LONGITUDE: location[1]},
    )
    old_location = (52.1, 5.1)
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: old_location[0],
            CONF_LONGITUDE: old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    assert result["step_id"] == "reconfigure_entity_location"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert provider.validation_calls == [(*location, 500)]
    assert entry.data[CONF_LOCATION_SOURCE] == LOCATION_ENTITY
    assert entry.data[CONF_LOCATION_ENTITY] == entity_id
    assert CONF_LATITUDE not in entry.data
    assert CONF_LONGITUDE not in entry.data
    assert entry.unique_id == config_flow._config_unique_id_entity(
        PROVIDER_NDW_DOT_NL, entity_id, 500
    )


@pytest.mark.asyncio
async def test_reconfigure_entity_keeps_form_open_on_provider_error(hass, monkeypatch):
    entity_id = "sensor.vehicle_location"
    location = (52.2, 5.2)
    hass.states.async_set(
        entity_id,
        "moving",
        {CONF_LATITUDE: location[0], CONF_LONGITUDE: location[1]},
    )
    old_location = (52.1, 5.1)
    provider = FakeProvider()
    provider.validation_error = ProviderConnectionError("private details")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: old_location[0],
            CONF_LONGITUDE: old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_entity_location"
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_LOCATION_SOURCE] == LOCATION_MANUAL
    assert provider.validation_calls == [(*location, 500)]


@pytest.mark.asyncio
async def test_reconfigure_rejects_entity_without_current_coordinates(
    hass, monkeypatch
):
    entity_id = "sensor.vehicle_location"
    hass.states.async_set(
        entity_id,
        "unavailable",
        {CONF_LATITUDE: 52.2, CONF_LONGITUDE: 5.2},
    )
    old_location = (52.1, 5.1)
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            CONF_LATITUDE: old_location[0],
            CONF_LONGITUDE: old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure_entity_location"
    assert result["errors"] == {"base": "invalid_entity_location"}
    assert entry.data[CONF_LOCATION_SOURCE] == LOCATION_MANUAL
    assert provider.validation_calls == []


@pytest.mark.asyncio
async def test_reconfigure_missing_entity_requires_an_explicit_replacement(
    hass, monkeypatch
):
    missing_entity = "sensor.old_vehicle_location"
    replacement_entity = "sensor.new_vehicle_location"
    location = (52.2, 5.2)
    hass.states.async_set(
        replacement_entity,
        "moving",
        {CONF_LATITUDE: location[0], CONF_LONGITUDE: location[1]},
    )
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id_entity(
            PROVIDER_NDW_DOT_NL, missing_entity, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_ENTITY,
            CONF_LOCATION_ENTITY: missing_entity,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    assert result["step_id"] == "reconfigure_entity_location"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: replacement_entity}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert provider.validation_calls == [(*location, 500)]
    assert entry.data[CONF_LOCATION_ENTITY] == replacement_entity
    assert entry.unique_id == config_flow._config_unique_id_entity(
        PROVIDER_NDW_DOT_NL, replacement_entity, 500
    )


@pytest.mark.asyncio
async def test_reconfigure_entity_unique_id_is_stable_when_coordinates_change(
    hass, monkeypatch
):
    entity_id = "sensor.vehicle_location"
    location = (52.3, 5.3)
    hass.states.async_set(
        entity_id,
        "moving",
        {CONF_LATITUDE: location[0], CONF_LONGITUDE: location[1]},
    )
    unique_id = config_flow._config_unique_id_entity(
        PROVIDER_NDW_DOT_NL, entity_id, 500
    )
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={
            CONF_LOCATION_SOURCE: LOCATION_ENTITY,
            CONF_LOCATION_ENTITY: entity_id,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_ENTITY}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_ENTITY: entity_id}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert provider.validation_calls == [(*location, 500)]
    assert entry.unique_id == unique_id


@pytest.mark.asyncio
async def test_reconfigure_can_switch_to_home_without_storing_coordinates(
    hass, monkeypatch
):
    hass.config.latitude = 52.3
    hass.config.longitude = 5.3
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)
    old_location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": old_location[0],
            "longitude": old_location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert provider.validation_calls == [(52.3, 5.3, 500)]
    assert entry.data[CONF_LOCATION_SOURCE] == LOCATION_HOME
    assert "latitude" not in entry.data
    assert "longitude" not in entry.data


@pytest.mark.asyncio
async def test_reconfigure_rejects_a_search_area_owned_by_another_entry(
    hass, monkeypatch
):
    location = (52.2, 5.2)
    unique_id = config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500)
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=unique_id,
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    existing_entry.add_to_hass(hass)
    old_location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(
            PROVIDER_NDW_DOT_NL, *old_location, 500
        ),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": old_location[0],
            "longitude": old_location[1],
        },
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: FakeProvider())

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"latitude": location[0], "longitude": location[1]}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_reconfigure_home_location_requires_home_coordinates(hass):
    hass.config.latitude = 0
    hass.config.longitude = 0
    location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "location_not_configured"


@pytest.mark.asyncio
async def test_reconfigure_keeps_the_form_open_on_provider_error(hass, monkeypatch):
    location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    entry.add_to_hass(hass)
    provider = FakeProvider()
    provider.validation_error = ProviderConnectionError("private details")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"latitude": 52.2, "longitude": 5.2}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_reconfigure_home_keeps_the_form_open_on_provider_error(
    hass, monkeypatch
):
    hass.config.latitude = 52.2
    hass.config.longitude = 5.2
    location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    entry.add_to_hass(hass)
    provider = FakeProvider()
    provider.validation_error = ProviderConnectionError("private details")
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_user_flow_reports_missing_home_coordinates(hass):
    hass.config.latitude = 0
    hass.config.longitude = 0
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "location_not_configured"


@pytest.mark.asyncio
async def test_reconfigure_to_manual_falls_back_when_no_coordinates_are_saved(hass):
    hass.config.latitude = 0
    hass.config.longitude = 0
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="home-search-area",
        data={
            CONF_LOCATION_SOURCE: LOCATION_HOME,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )

    assert result["step_id"] == "reconfigure_manual_location"


@pytest.mark.asyncio
async def test_reconfigure_manual_step_aborts_without_submitted_coordinates(hass):
    location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_MANUAL}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], None)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_not_configured"


@pytest.mark.asyncio
async def test_reconfigure_manual_step_rejects_invalid_coordinates(hass):
    location = (52.1, 5.1)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=config_flow._config_unique_id(PROVIDER_NDW_DOT_NL, *location, 500),
        data={
            CONF_LOCATION_SOURCE: LOCATION_MANUAL,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_SEARCH_RADIUS: 500,
            CONF_MAX_STATIONS: 10,
            "latitude": location[0],
            "longitude": location[1],
        },
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": entry.entry_id},
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]

    result = await flow.async_step_reconfigure_manual_location(
        {"latitude": 91, "longitude": 5}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_location"


@pytest.mark.asyncio
async def test_manual_step_rejects_invalid_coordinates(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]

    result = await flow.async_step_manual_location({"latitude": 91, "longitude": 5})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_location"


@pytest.mark.asyncio
async def test_station_options_aborts_if_no_location_was_selected(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    flow = hass.config_entries.flow._progress[result["flow_id"]]

    result = await flow.async_step_station_options(_station_options())

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "location_not_configured"


@pytest.mark.parametrize(
    ("error", "translation"),
    [
        (AuthenticationError("auth"), "auth"),
        (ProviderRateLimitError("rate limited", retry_after=60), "rate_limited"),
        (InvalidProviderResponseError("invalid"), "invalid_response"),
        (UnsupportedLocationError("location"), "invalid_location"),
        (UnsupportedProviderError("provider"), "unsupported_provider"),
        (ProviderConnectionError("connection"), "cannot_connect"),
        (ChargingProviderError("unknown"), "cannot_connect"),
    ],
)
def test_provider_errors_map_to_user_facing_translations(error, translation):
    assert config_flow._flow_error(error) == translation


def test_home_location_is_unknown_without_configured_coordinates(hass):
    hass.config.latitude = None
    hass.config.longitude = None

    assert config_flow._home_location(hass) is None
