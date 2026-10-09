"""Tests for CurbCharge setup and options flows."""

import pytest
import voluptuous as vol
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.curbcharge import config_flow
from custom_components.curbcharge.const import (
    CONF_INCLUDE_UNKNOWN,
    CONF_LOCATION_SOURCE,
    CONF_MAX_STATIONS,
    CONF_PROVIDER,
    CONF_SCAN_INTERVAL,
    CONF_SEARCH_RADIUS,
    DOMAIN,
    LOCATION_HOME,
    LOCATION_MANUAL,
    PROVIDER_NDW_DOT_NL,
)
from custom_components.curbcharge.provider import AuthenticationError
from tests.conftest import FakeProvider

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _station_options() -> dict[str, int | str]:
    return {
        CONF_SEARCH_RADIUS: 500,
        CONF_MAX_STATIONS: 10,
        CONF_PROVIDER: PROVIDER_NDW_DOT_NL,
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
