"""Tests for repository metadata that HACS and the Marketplace read."""

import json
from pathlib import Path

from custom_components.curbcharge.const import SUPPORTED_COUNTRY_CODES

REPOSITORY_ROOT = Path(__file__).parent.parent


def test_hacs_country_list_matches_supported_countries():
    """HACS hides the repository from users filtering on an omitted country."""
    hacs = json.loads((REPOSITORY_ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert sorted(hacs["country"]) == sorted(SUPPORTED_COUNTRY_CODES)
