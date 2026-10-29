"""Tests for translation completeness."""

import json
from pathlib import Path
from string import Formatter
from typing import Any

import pytest

SUPPORTED_LANGUAGES = ("en", "nl", "fr", "de", "nb", "sv", "fi")
AFDC_CONNECTOR_TRANSLATION_KEYS = (
    "iec_62196_t1",
    "iec_62196_t1_combo",
    "tesla",
    "nema_5_15",
    "nema_5_20",
    "nema_14_50",
    "sae_j3271",
)


def _leaf_values(
    value: Any, prefix: tuple[str, ...] = ()
) -> dict[tuple[str, ...], Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    return {
        path: leaf
        for key, child in value.items()
        for path, leaf in _leaf_values(child, (*prefix, key)).items()
    }


def _placeholder_names(value: str) -> set[str]:
    return {name for _, name, _, _ in Formatter().parse(value) if name is not None}


def test_english_translation_matches_strings():
    integration = Path(__file__).parent.parent / "custom_components" / "curbcharge"
    source = json.loads((integration / "strings.json").read_text(encoding="utf-8"))
    english = json.loads(
        (integration / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert source == english


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_translations_match_english_keys_values_and_placeholders(language):
    integration = Path(__file__).parent.parent / "custom_components" / "curbcharge"
    english = _leaf_values(
        json.loads(
            (integration / "translations" / "en.json").read_text(encoding="utf-8")
        )
    )
    translated = _leaf_values(
        json.loads(
            (integration / "translations" / f"{language}.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert english.keys() == translated.keys()
    for path, value in translated.items():
        assert isinstance(value, str) and value.strip(), (language, path)
        assert _placeholder_names(english[path]) == _placeholder_names(value), (
            language,
            path,
        )
    for step_id in ("nobil_credentials", "nobil_reauth_confirm"):
        assert (
            "({nobil_api_url})"
            in translated[("config", "step", step_id, "description")]
        )
    for step_id in ("general", "personalization"):
        assert (
            translated[("options", "step", "init", "menu_options", step_id)]
            == translated[("options", "step", step_id, "title")]
        )
    for connector_key in AFDC_CONNECTOR_TRANSLATION_KEYS:
        assert translated[
            ("selector", "personalization_connector_type", "options", connector_key)
        ].strip()
