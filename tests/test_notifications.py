"""Tests for CurbCharge persistent notifications."""

from unittest.mock import AsyncMock, Mock, call

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.curbcharge import notifications
from custom_components.curbcharge.const import DOMAIN


@pytest.mark.asyncio
async def test_error_notification_uses_translated_message(hass, monkeypatch):
    translations = {
        f"component.{DOMAIN}.exceptions.rate_limited.message": "Provider is busy."
    }
    get_translations = AsyncMock(return_value=translations)
    create = Mock()
    monkeypatch.setattr(notifications, "async_get_translations", get_translations)
    monkeypatch.setattr(notifications.persistent_notification, "async_create", create)

    await notifications.async_create_error_notification(
        hass, "entry-1", "provider", "rate_limited"
    )

    get_translations.assert_awaited_once_with(
        hass, hass.config.language, "exceptions", integrations={DOMAIN}
    )
    create.assert_called_once_with(
        hass,
        "Provider is busy.",
        title="CurbCharge",
        notification_id=f"{DOMAIN}_entry-1_provider",
    )


@pytest.mark.asyncio
async def test_error_notification_falls_back_when_message_key_is_missing(
    hass, monkeypatch, caplog
):
    translations = {
        f"component.{DOMAIN}.exceptions.notification_error.message": "Generic error."
    }
    monkeypatch.setattr(
        notifications,
        "async_get_translations",
        AsyncMock(return_value=translations),
    )
    create = Mock()
    monkeypatch.setattr(notifications.persistent_notification, "async_create", create)

    with caplog.at_level("ERROR", logger=notifications.__name__):
        await notifications.async_create_error_notification(
            hass, "entry-2", "service", "missing_key"
        )

    assert "Missing CurbCharge notification translation: missing_key" in caplog.text
    assert create.call_args.args[1] == "Generic error."
    assert create.call_args.kwargs["notification_id"] == f"{DOMAIN}_entry-2_service"


@pytest.mark.asyncio
async def test_error_notification_uses_safe_fallback_if_translations_fail(
    hass, monkeypatch, caplog
):
    monkeypatch.setattr(
        notifications,
        "async_get_translations",
        AsyncMock(side_effect=HomeAssistantError("translation unavailable")),
    )
    create = Mock()
    monkeypatch.setattr(notifications.persistent_notification, "async_create", create)

    with caplog.at_level("ERROR", logger=notifications.__name__):
        await notifications.async_create_error_notification(
            hass, "entry-3", "setup", "setup_failed"
        )

    assert "Could not load CurbCharge notification translations" in caplog.text
    assert create.call_args.args[1] == notifications._FALLBACK_MESSAGE
    assert create.call_args.kwargs["notification_id"] == f"{DOMAIN}_entry-3_setup"


def test_dismiss_error_notifications_targets_all_entry_categories(hass, monkeypatch):
    dismiss = Mock()
    monkeypatch.setattr(notifications.persistent_notification, "async_dismiss", dismiss)

    notifications.async_dismiss_error_notifications(hass, "entry-4")

    assert dismiss.call_args_list == [
        call(hass, f"{DOMAIN}_entry-4_provider"),
        call(hass, f"{DOMAIN}_entry-4_setup"),
        call(hass, f"{DOMAIN}_entry-4_service"),
    ]
