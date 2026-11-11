"""Tests for the documented native CurbCharge dashboard YAML."""

from pathlib import Path

import yaml
from homeassistant.helpers.template import Template

REPOSITORY_ROOT = Path(__file__).parent.parent
README_PATH = REPOSITORY_ROOT / "README.md"
GUIDE_PATH = REPOSITORY_ROOT / "docs" / "dashboards-and-automations.md"
DASHBOARD_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-dashboard.yaml"
MAP_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-map.yaml"
NAVIGATION_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-navigation.yaml"


def _yaml_block_containing(text, marker):
    marker_index = text.index(marker)
    block_start = text.rfind("```yaml\n", 0, marker_index)
    assert block_start >= 0
    content_start = block_start + len("```yaml\n")
    block_end = text.index("\n```", marker_index)
    return yaml.safe_load(text[content_start:block_end])


def test_documented_dashboard_examples_parse_and_link_to_canonical_files():
    readme = README_PATH.read_text(encoding="utf-8")
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    lovelace_section = guide.split("## Lovelace card", 1)[1].split(
        "## Use cases and automations", 1
    )[0]
    assert all(
        f"]({path})" in readme
        for path in (
            "docs/usage.md",
            "docs/dashboards-and-automations.md",
            "docs/development.md",
        )
    )
    dashboard = yaml.safe_load(DASHBOARD_PATH.read_text(encoding="utf-8"))
    map_example = yaml.safe_load(MAP_PATH.read_text(encoding="utf-8"))
    navigation_example = yaml.safe_load(NAVIGATION_PATH.read_text(encoding="utf-8"))
    assert "](../examples/curbcharge-dashboard.yaml)" in lovelace_section
    assert "](../examples/curbcharge-map.yaml)" in lovelace_section
    assert "](../examples/curbcharge-navigation.yaml)" in lovelace_section
    assert all(
        isinstance(example, dict)
        for example in (dashboard, map_example, navigation_example)
    )

    cards = dashboard["views"][0]["cards"]
    assert {card["type"] for card in cards} == {"markdown", "entities"}

    entities_cards = [card for card in cards if card["type"] == "entities"]
    recommendation_rows = entities_cards[0]["entities"]
    displayed_attributes = {
        row["attribute"]
        for row in recommendation_rows
        if row.get("type") == "attribute"
    }
    assert displayed_attributes >= {
        "result_status",
        "best_nearby_rank",
        "best_nearby_rank_score",
        "best_nearby_candidate_count",
        "is_favorite",
        "address",
        "available_connectors",
        "total_connectors",
        "distance_m",
        "max_power_kw",
        "price_per_kwh",
        "currency",
        "provider_last_updated",
        "last_successful_update",
    }

    aggregate_entities = {row["entity"] for row in entities_cards[1]["entities"]}
    assert aggregate_entities >= {
        "sensor.your_nearby_charging_units",
        "sensor.your_nearby_available_connectors",
        "sensor.your_nearby_availability_percentage",
    }

    favorite_rows = entities_cards[2]["entities"]
    assert {row.get("entity") for row in favorite_rows} >= {
        "sensor.your_favorite_summary",
        "sensor.your_favorite_status",
    }
    favorite_attributes = {
        row["attribute"] for row in favorite_rows if row.get("type") == "attribute"
    }
    assert favorite_attributes >= {
        "configured_count",
        "discovered_count",
        "available_count",
        "unknown_count",
        "stale_count",
        "missing_count",
        "available_connectors",
        "price_per_kwh",
        "currency",
        "is_stale",
    }

    favorite_guide = guide.split("### Favorite services and automations", 1)[1].split(
        "### Availability notifications", 1
    )[0]
    assert "curbcharge.add_favorite" in favorite_guide
    assert "curbcharge.remove_favorite" in favorite_guide
    assert "notify.notify" in favorite_guide
    assert "provider_update_success" in favorite_guide
    assert "missing_refresh_count" in favorite_guide

    map_entities = {row["entity"] for row in map_example["entities"]}
    assert map_entities >= {
        "sensor.your_best_nearby_charger",
        "sensor.your_station_status_summary",
    }
    assert all(entity.startswith("sensor.") for entity in map_entities)

    navigation_template = navigation_example["content"]
    assert "state_attr(source_entity, 'latitude')" in navigation_template
    assert "state_attr(source_entity, 'longitude')" in navigation_template
    assert "origin=" not in navigation_template
    assert "source=" not in navigation_template

    native_section, optional_section = lovelace_section.split(
        "### Optional: dynamic station list with Auto-Entities", 1
    )
    assert "custom:auto-entities" not in native_section
    assert "custom:auto-entities" in optional_section


def test_documented_personalization_templates_and_trigger_parse():
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    section = guide.split("### Personalization templates and triggers", 1)[1].split(
        "### Favorite services and automations", 1
    )[0]
    assert "preferred_operator" in section
    assert "fallback_used" in section
    assert "IEC_62196_T2_COMBO" in section
    assert "no_personalized_match" in section
    assert "provider_station_id" in section
    assert "does not assume a notification platform" in section

    trigger = _yaml_block_containing(
        section, "entity_id: sensor.your_best_nearby_charger"
    )
    assert trigger["trigger"][0]["platform"] == "state"
    assert trigger["condition"][0]["condition"] == "template"
    assert "provider_station_id" in trigger["condition"][0]["value_template"]


def test_coverage_aware_availability_history_example(hass):
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    config = _yaml_block_containing(guide, "name: Station available time today")

    available_time, known_time = config["sensor"]
    assert available_time["state"] == "on"
    assert available_time["type"] == known_time["type"] == "time"
    assert known_time["state"] == ["on", "off"]
    assert available_time["start"] == known_time["start"]
    assert available_time["end"] == known_time["end"]

    templates = {
        sensor["name"]: sensor["state"] for sensor in config["template"][0]["sensor"]
    }
    availability = Template(
        templates["Station availability among known time today"], hass
    )
    coverage = Template(templates["Station known data coverage today"], hass)

    assert availability.async_render().strip() == "unknown"
    assert coverage.async_render().strip() == "unknown"

    hass.states.async_set(
        "sensor.station_known_availability_time_today",
        "4",
    )
    hass.states.async_set("sensor.station_available_time_today", "0")
    assert float(availability.async_render()) == 0

    period_hours = float(
        Template(
            "{{ (as_timestamp(now()) - as_timestamp(today_at())) / 3600 }}", hass
        ).async_render()
    )
    hass.states.async_set(
        "sensor.station_known_availability_time_today",
        str(period_hours / 2),
    )
    hass.states.async_set(
        "sensor.station_available_time_today",
        str(period_hours / 4),
    )
    assert float(availability.async_render()) == 50
    if period_hours > 0:
        assert float(coverage.async_render()) == 50
    else:
        assert coverage.async_render().strip() == "unknown"
