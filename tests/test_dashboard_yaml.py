"""Tests for the documented native CurbCharge dashboard YAML."""

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parent.parent
README_PATH = REPOSITORY_ROOT / "README.md"
GUIDE_PATH = REPOSITORY_ROOT / "docs" / "dashboards-and-automations.md"
DASHBOARD_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-dashboard.yaml"
MAP_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-map.yaml"
NAVIGATION_PATH = REPOSITORY_ROOT / "examples" / "curbcharge-navigation.yaml"


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
