"""Generate safe, distinguishable CurbCharge config-entry titles."""

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENTRY_NAME,
    CONF_LOCATION_ENTITY,
    CONF_LOCATION_SOURCE,
    CONF_PROVIDER,
    LOCATION_ENTITY,
    LOCATION_HOME,
    PROVIDER_AFDC_US,
    PROVIDER_AFDC_US_NAME,
    PROVIDER_CHARGY_LU,
    PROVIDER_CHARGY_LU_NAME,
    PROVIDER_DIGITRAFFIC_AFIR_FI,
    PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
    PROVIDER_ECO_MOVEMENT_BE,
    PROVIDER_ECO_MOVEMENT_BE_NAME,
    PROVIDER_ICH_TANKE_STROM_CH,
    PROVIDER_ICH_TANKE_STROM_CH_NAME,
    PROVIDER_NAME,
    PROVIDER_NDW_DOT_NL,
    PROVIDER_NOBIL_NO,
    PROVIDER_NOBIL_NO_NAME,
    PROVIDER_NOBIL_SE,
    PROVIDER_NOBIL_SE_NAME,
    PROVIDER_OPEN_CHARGE_MAP,
    PROVIDER_OPEN_CHARGE_MAP_NAME,
    PROVIDER_VIA_LIETUVA_LT,
    PROVIDER_VIA_LIETUVA_LT_NAME,
)

_PROVIDER_NAMES = {
    PROVIDER_NDW_DOT_NL: PROVIDER_NAME,
    PROVIDER_ECO_MOVEMENT_BE: PROVIDER_ECO_MOVEMENT_BE_NAME,
    PROVIDER_NOBIL_NO: PROVIDER_NOBIL_NO_NAME,
    PROVIDER_NOBIL_SE: PROVIDER_NOBIL_SE_NAME,
    PROVIDER_AFDC_US: PROVIDER_AFDC_US_NAME,
    PROVIDER_DIGITRAFFIC_AFIR_FI: PROVIDER_DIGITRAFFIC_AFIR_FI_NAME,
    PROVIDER_ICH_TANKE_STROM_CH: PROVIDER_ICH_TANKE_STROM_CH_NAME,
    PROVIDER_CHARGY_LU: PROVIDER_CHARGY_LU_NAME,
    PROVIDER_VIA_LIETUVA_LT: PROVIDER_VIA_LIETUVA_LT_NAME,
    PROVIDER_OPEN_CHARGE_MAP: PROVIDER_OPEN_CHARGE_MAP_NAME,
}


def normalize_entry_name(value: object) -> str:
    """Trim an optional user-supplied entry name."""
    return value.strip() if isinstance(value, str) else ""


def entry_title(hass: HomeAssistant, data: Mapping[str, Any]) -> str:
    """Return a custom title or one based on the provider and safe location label."""
    if custom_name := normalize_entry_name(data.get(CONF_ENTRY_NAME)):
        return custom_name

    provider_id = data.get(CONF_PROVIDER)
    provider_name = (
        _PROVIDER_NAMES.get(provider_id, "Charging data")
        if isinstance(provider_id, str)
        else "Charging data"
    )
    location_source = data.get(CONF_LOCATION_SOURCE)
    if location_source == LOCATION_HOME:
        location_name = "Home"
    elif location_source == LOCATION_ENTITY:
        entity_id = data.get(CONF_LOCATION_ENTITY)
        state = hass.states.get(entity_id) if isinstance(entity_id, str) else None
        friendly_name = (
            state.attributes.get("friendly_name") if state is not None else None
        )
        location_name = (
            normalize_entry_name(friendly_name)
            or (entity_id if isinstance(entity_id, str) else "")
            or "Location entity"
        )
    else:
        location_name = "Manual location"

    return f"CurbCharge - {provider_name} ({location_name})"
