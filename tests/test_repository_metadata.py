"""Tests for repository metadata that HACS and the Marketplace read."""

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parent.parent


def test_hacs_does_not_restrict_global_provider_discovery():
    """Global OCM coverage must not be hidden by HACS country filtering."""
    hacs = json.loads((REPOSITORY_ROOT / "hacs.json").read_text(encoding="utf-8"))

    assert "country" not in hacs
