"""Tests for the native navigation example."""

from pathlib import Path

import yaml
from homeassistant.helpers.template import Template

NAVIGATION_PATH = (
    Path(__file__).parent.parent / "examples" / "curbcharge-navigation.yaml"
)
EXAMPLE_ENTITY = "sensor.your_best_nearby_charger"


def _render_navigation(hass, entity_id: str, state: str, attributes: dict) -> str:
    """Render the documented Markdown template for one entity state."""
    hass.states.async_set(entity_id, state, attributes)
    config = yaml.safe_load(NAVIGATION_PATH.read_text(encoding="utf-8"))
    source = config["content"].replace(EXAMPLE_ENTITY, entity_id)
    return Template(source, hass).async_render()


def test_navigation_template_builds_encoded_provider_links(hass):
    rendered = _render_navigation(
        hass,
        EXAMPLE_ENTITY,
        "Example station",
        {
            "result_status": "selected",
            "latitude": 0.0,
            "longitude": -73.9,
            "address": "Example Street 10",
        },
    )

    assert "https://www.google.com/maps/dir/?api=1&destination=0.0%2C-73.9" in rendered
    assert "https://maps.apple.com/directions?destination=0.0%2C-73.9" in rendered
    assert "https://waze.com/ul?ll=0.0%2C-73.9&navigate=yes" in rendered
    assert "[Open map location (not directions)](geo:0.0,-73.9?q=0.0,-73.9)" in rendered
    assert "origin=" not in rendered
    assert "source=" not in rendered


def test_navigation_template_uses_encoded_address_when_coordinates_are_invalid(hass):
    rendered = _render_navigation(
        hass,
        EXAMPLE_ENTITY,
        "Example station",
        {
            "result_status": "selected",
            "latitude": 91,
            "longitude": -181,
            "address": "R&D Road 10",
        },
    )

    assert "destination=R%26D%20Road%2010" in rendered
    assert "https://waze.com/ul?q=R%26D%20Road%2010&navigate=yes" in rendered
    assert (
        "[Open map location (not directions)](geo:0,0?q=R%26D%20Road%2010)" in rendered
    )


def test_navigation_template_allows_unknown_summary_availability(hass):
    rendered = _render_navigation(
        hass,
        "sensor.station_status_summary",
        "unknown",
        {
            "provider_station_id": "station-1",
            "latitude": 0.0,
            "longitude": 0.0,
        },
    )

    assert "Google Maps directions" in rendered
    assert "destination=0.0%2C0.0" in rendered


def test_navigation_template_hides_no_result_unavailable_and_missing_locations(hass):
    cases = (
        (
            EXAMPLE_ENTITY,
            "unknown",
            {
                "result_status": "no_current_stations",
                "latitude": 52.0,
                "longitude": 5.0,
            },
        ),
        (
            EXAMPLE_ENTITY,
            "unavailable",
            {
                "result_status": "selected",
                "latitude": 52.0,
                "longitude": 5.0,
            },
        ),
        (
            "sensor.station_status_summary",
            "unknown",
            {"provider_station_id": "station-1"},
        ),
    )

    for entity_id, state, attributes in cases:
        rendered = _render_navigation(hass, entity_id, state, attributes)
        assert "Google Maps directions" not in rendered
        assert "Apple Maps directions" not in rendered
        assert "Waze navigation" not in rendered
