"""Tests for CurbCharge setup and options flows."""

import pytest
import voluptuous as vol
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge import config_flow
from custom_components.curbcharge.const import (
    CONF_API_TOKEN,
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_MANUAL,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_NDW_DOT_NL,
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

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _station_options(
    provider: str = PROVIDER_NDW_DOT_NL,
) -> dict[str, int | str]:
    return {
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: provider,
    }


@pytest.mark.asyncio
async def test_config_flow_uses_home_coordinates(hass, monkeypatch):
    hass.config.latitude = 52.1
    hass.config.longitude = 5.1
    provider = FakeProvider()
    monkeypatch.setattr(config_flow, "create_provider", lambda *_args: provider)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LOCATION_SOURCE: LOCATION_HOME}
    )
    assert result["step_id"] == "station_options"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _station_options()
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert provider.validation_calls == [(52.1, 5.1, 500)]
    assert result["data"][CONF_LOCATION_SOURCE] == LOCATION_HOME
    assert "latitude" not in result["data"]
    assert "longitude" not in result["data"]
    assert "52.1" not in result["result"].unique_id


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

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert provider.validation_calls == [(52.123456, 5.654321, 500)]
    assert result["data"]["latitude"] == 52.123456
    assert result["data"]["longitude"] == 5.654321
    assert "52.123456" not in result["result"].unique_id
    assert "5.654321" not in result["result"].unique_id


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
    assert provider.validation_calls == [(50.85, 4.35, 500)]


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
    with pytest.raises(vol.Invalid):
        config_flow.manual_location_schema()({"latitude": 91, "longitude": 5})
    with pytest.raises(vol.Invalid):
        config_flow.manual_location_schema()({"latitude": 52, "longitude": 181})
    with pytest.raises(vol.Invalid):
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
    with pytest.raises(vol.Invalid):
        config_flow.station_options_schema()(
            {
                CONF_SEARCH_RADIUS: 500,
                CONF_MAX_STATIONS: 51,
                CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            }
        )


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
    with pytest.raises(vol.Invalid):
        schema({**valid, CONF_SCAN_INTERVAL: 0})


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

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_INTERVAL: 1.0,
            CONF_SEARCH_RADIUS: 100.0,
            CONF_MAX_STATIONS: 5.0,
            CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
            CONF_INCLUDE_UNKNOWN: False,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        CONF_SCAN_INTERVAL: 1,
        CONF_SEARCH_RADIUS: 100,
        CONF_MAX_STATIONS: 5,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
        CONF_INCLUDE_UNKNOWN: False,
    }


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

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "auth"


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
