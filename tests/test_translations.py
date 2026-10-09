"""Tests for translation completeness."""

import json
from pathlib import Path
from typing import Any


def _leaf_paths(value: Any, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    if not isinstance(value, dict):
        return {prefix}
    return {
        leaf
        for key, child in value.items()
        for leaf in _leaf_paths(child, (*prefix, key))
    }


def test_english_translation_matches_strings():
    integration = Path(__file__).parent.parent / "custom_components" / "curbcharge"
    source = json.loads((integration / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (integration / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert source == english


def test_dutch_translation_has_the_same_keys_as_english():
    integration = Path(__file__).parent.parent / "custom_components" / "curbcharge"
    english = json.loads(
        (integration / "translations" / "en.json").read_text(encoding="utf-8")
    )
    dutch = json.loads(
        (integration / "translations" / "nl.json").read_text(encoding="utf-8")
    )

    assert _leaf_paths(english) == _leaf_paths(dutch)
