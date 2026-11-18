"""CurbCharge persistent error notifications."""

import logging
from typing import Literal

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

type ErrorCategory = Literal["provider", "setup", "service"]
_ERROR_CATEGORIES: tuple[ErrorCategory, ...] = ("provider", "setup", "service")
_FALLBACK_MESSAGE = "A CurbCharge error occurred. Check the Home Assistant logs."


async def async_create_error_notification(
    hass: HomeAssistant,
    entry_id: str,
    category: ErrorCategory,
    translation_key: str,
) -> None:
    """Create or update one localized notification for an entry and error kind."""
    message: str
    try:
        translations = await async_get_translations(
            hass,
            hass.config.language,
            "exceptions",
            integrations={DOMAIN},
        )
    except (HomeAssistantError, OSError):
        _LOGGER.exception("Could not load CurbCharge notification translations")
        message = _FALLBACK_MESSAGE
    else:
        message_key = f"component.{DOMAIN}.exceptions.{translation_key}.message"
        translated_message = translations.get(message_key)
        if translated_message is None:
            _LOGGER.error(
                "Missing CurbCharge notification translation: %s", translation_key
            )
            message = translations.get(
                f"component.{DOMAIN}.exceptions.notification_error.message",
                _FALLBACK_MESSAGE,
            )
        else:
            message = translated_message

    persistent_notification.async_create(
        hass,
        message,
        title="CurbCharge",
        notification_id=_notification_id(entry_id, category),
    )


def async_dismiss_error_notification(
    hass: HomeAssistant, entry_id: str, category: ErrorCategory
) -> None:
    """Dismiss one CurbCharge notification."""
    persistent_notification.async_dismiss(hass, _notification_id(entry_id, category))


def async_dismiss_error_notifications(hass: HomeAssistant, entry_id: str) -> None:
    """Dismiss all CurbCharge notifications belonging to an entry."""
    for category in _ERROR_CATEGORIES:
        async_dismiss_error_notification(hass, entry_id, category)


def _notification_id(entry_id: str, category: ErrorCategory) -> str:
    return f"{DOMAIN}_{entry_id}_{category}"
