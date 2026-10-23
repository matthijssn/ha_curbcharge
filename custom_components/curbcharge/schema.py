"""Schema library compatibility for configuration flows and services."""

from importlib import import_module
from typing import Any


def load_schema_module() -> Any:
    """Select the schema library available in the current Home Assistant."""
    try:
        return import_module("probatio")
    except ModuleNotFoundError as err:
        if err.name != "probatio":
            raise
        return import_module("voluptuous")


vol = load_schema_module()
