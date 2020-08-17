"""Tests for Sentry integration."""
import logging

import pytest

from homeassistant.components.sentry import get_channel, process_before_send
from homeassistant.components.sentry.const import CONF_DSN, CONF_ENVIRONMENT, DOMAIN
from homeassistant.const import __version__ as current_version
from homeassistant.core import HomeAssistant

from tests.async_mock import Mock, patch
from tests.common import MockConfigEntry


async def test_setup_entry(hass: HomeAssistant) -> None:
    """Test integration setup from entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DSN: "http://public@example.com/1", CONF_ENVIRONMENT: "production"},
    )
    entry.add_to_hass(hass)

    with patch(
        "homeassistant.components.sentry.AioHttpIntegration"
    ) as sentry_aiohttp_mock, patch(
        "homeassistant.components.sentry.SqlalchemyIntegration"
    ) as sentry_sqlalchemy_mock, patch(
        "homeassistant.components.sentry.LoggingIntegration"
    ) as sentry_logging_mock, patch(
        "homeassistant.components.sentry.sentry_sdk"
    ) as sentry_mock:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert sentry_logging_mock.call_count == 1
    assert sentry_logging_mock.called_once_with(
        level=logging.WARNING, event_level=logging.WARNING
    )

    assert sentry_aiohttp_mock.call_count == 1
    assert sentry_sqlalchemy_mock.call_count == 1
    assert sentry_mock.init.call_count == 1

    call_args = sentry_mock.init.call_args[1]
    assert set(call_args) == {
        "dsn",
        "environment",
        "integrations",
        "release",
        "before_send",
    }
    assert call_args["dsn"] == "http://public@example.com/1"
    assert call_args["environment"] == "production"
    assert call_args["integrations"] == [
        sentry_logging_mock.return_value,
        sentry_aiohttp_mock.return_value,
        sentry_sqlalchemy_mock.return_value,
    ]
    assert call_args["release"] == current_version
    assert call_args["before_send"]


@pytest.mark.parametrize(
    "version,channel",
    [
        ("0.115.0.dev20200815", "nightly"),
        ("0.115.0", "stable"),
        ("0.115.0b4", "beta"),
        ("0.115.0dev0", "dev"),
    ],
)
async def test_get_channel(version, channel) -> None:
    """Test if channel detection works from Home Assistant version number."""
    assert get_channel(version) == channel


async def test_process_before_send(hass: HomeAssistant):
    """Test regular use of the Sentry process before sending function."""
    hass.config.components.add("puppies")
    hass.config.components.add("a_integration")

    # These should not show up in the result.
    hass.config.components.add("puppies.light")
    hass.config.components.add("auth")

    result = process_before_send(
        hass,
        channel="test",
        huuid="12345",
        system_info={"installation_type": "pytest"},
        custom_components=["ironing_robot", "fridge_opener"],
        event={},
        hint={},
    )

    assert result
    assert result["tags"]
    assert result["contexts"]
    assert result["contexts"]

    ha_context = result["contexts"]["Home Assistant"]
    assert ha_context["channel"] == "test"
    assert ha_context["custom_components"] == "fridge_opener\nironing_robot"
    assert ha_context["integrations"] == "a_integration\npuppies"

    tags = result["tags"]
    assert tags["channel"] == "test"
    assert tags["uuid"] == "12345"
    assert tags["installation_type"] == "pytest"


async def test_event_with_platform_context(hass: HomeAssistant):
    """Test extraction of platform context information during Sentry events."""

    current_platform_mock = Mock()
    current_platform_mock.get().platform_name = "hue"
    current_platform_mock.get().domain = "light"

    with patch(
        "homeassistant.components.sentry.entity_platform.current_platform",
        new=current_platform_mock,
    ):
        result = process_before_send(
            hass,
            channel="test",
            huuid="12345",
            system_info={"installation_type": "pytest"},
            custom_components=["ironing_robot"],
            event={},
            hint={},
        )

    assert result
    assert result["tags"]["integration"] == "hue"
    assert result["tags"]["platform"] == "light"
    assert result["tags"]["custom_component"] == "no"

    current_platform_mock.get().platform_name = "ironing_robot"
    current_platform_mock.get().domain = "switch"

    with patch(
        "homeassistant.components.sentry.entity_platform.current_platform",
        new=current_platform_mock,
    ):
        result = process_before_send(
            hass,
            channel="test",
            huuid="12345",
            system_info={"installation_type": "pytest"},
            custom_components=["ironing_robot"],
            event={},
            hint={},
        )

    assert result
    assert result["tags"]["integration"] == "ironing_robot"
    assert result["tags"]["platform"] == "switch"
    assert result["tags"]["custom_component"] == "yes"


@pytest.mark.parametrize(
    "logger,tags",
    [
        ("adguard", {"package": "adguard"}),
        (
            "homeassistant.components.hue.coordinator",
            {"integration": "hue", "custom_component": "no"},
        ),
        (
            "homeassistant.components.hue.light",
            {"integration": "hue", "platform": "light", "custom_component": "no"},
        ),
        (
            "homeassistant.components.ironing_robot.switch",
            {
                "integration": "ironing_robot",
                "platform": "switch",
                "custom_component": "yes",
            },
        ),
        (
            "homeassistant.components.ironing_robot",
            {"integration": "ironing_robot", "custom_component": "yes"},
        ),
        ("homeassistant.helpers.network", {"helpers": "network"}),
        ("tuyapi.test", {"package": "tuyapi"}),
    ],
)
async def test_logger_event_extraction(hass: HomeAssistant, logger, tags):
    """Test extraction of information from Sentry logger events."""

    result = process_before_send(
        hass,
        channel="test",
        huuid="12345",
        system_info={"installation_type": "pytest"},
        custom_components=["ironing_robot"],
        event={"logger": logger},
        hint={},
    )

    assert result
    assert result["tags"] == {
        "channel": "test",
        "uuid": "12345",
        "installation_type": "pytest",
        **tags,
    }