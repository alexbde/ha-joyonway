"""Tests for the Joyonway P25B85 config flow and options flow."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.joyonway.config_flow import (
    JoyonwayP25B85ConfigFlow,
    JoyonwayP25B85OptionsFlow,
    _test_connection,
)
from custom_components.joyonway.const import (
    CONF_MODEL,
    DEFAULT_MODEL,
    DOMAIN,
    OPT_AUTO_SYNC_CLOCK,
    OPT_OZONE_MODE,
    OZONE_MODE_AUTO,
    OZONE_MODE_MANUAL,
)
from homeassistant.const import CONF_HOST, CONF_PORT


@pytest.mark.asyncio
async def test_test_connection_success() -> None:
    """Test connection helper when TCP connection succeeds."""
    mock_reader = MagicMock()
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection", return_value=(mock_reader, mock_writer)
    ) as mock_open:
        result = await _test_connection("127.0.0.1", 8899)
        assert result is True
        mock_open.assert_called_once_with("127.0.0.1", 8899)
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_connection_failure() -> None:
    """Test connection helper when TCP connection fails."""
    with patch(
        "asyncio.open_connection", side_effect=OSError("connection refused")
    ) as mock_open:
        result = await _test_connection("127.0.0.1", 8899)
        assert result is False
        mock_open.assert_called_once_with("127.0.0.1", 8899)


@pytest.mark.asyncio
async def test_config_flow_user_step_init() -> None:
    """Test config flow step user initializes with correct schema."""
    flow = JoyonwayP25B85ConfigFlow()
    flow.hass = MagicMock()

    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert not result["errors"]


@pytest.mark.asyncio
async def test_config_flow_user_step_success() -> None:
    """Test config flow step user completes successfully on valid connection."""
    flow = JoyonwayP25B85ConfigFlow()
    flow.hass = MagicMock()

    # Mock unique ID methods
    flow.async_set_unique_id = AsyncMock(return_value="127.0.0.1:8899")
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
    }

    with patch(
        "custom_components.joyonway.config_flow._test_connection",
        return_value=True,
    ) as mock_test:
        result = await flow.async_step_user(user_input)

        assert result["type"] == "create_entry"
        assert result["title"] == "Joyonway Spa (127.0.0.1)"
        assert result["data"] == {
            CONF_HOST: "127.0.0.1",
            CONF_PORT: 8899,
            CONF_MODEL: DEFAULT_MODEL,
        }
        mock_test.assert_called_once_with("127.0.0.1", 8899)
        flow.async_set_unique_id.assert_called_once_with("127.0.0.1:8899")
        flow._abort_if_unique_id_configured.assert_called_once()


@pytest.mark.asyncio
async def test_config_flow_user_step_cannot_connect() -> None:
    """Test config flow step user returns error on connection failure."""
    flow = JoyonwayP25B85ConfigFlow()
    flow.hass = MagicMock()

    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
    }

    with patch(
        "custom_components.joyonway.config_flow._test_connection",
        return_value=False,
    ):
        result = await flow.async_step_user(user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_options_flow_step_init_rendering() -> None:
    """Test options flow step init renders form with defaults."""
    flow = JoyonwayP25B85OptionsFlow()
    flow.hass = MagicMock()
    flow.handler = "test_entry"
    
    mock_entry = MagicMock()
    mock_entry.options = {
        OPT_OZONE_MODE: OZONE_MODE_MANUAL,
        OPT_AUTO_SYNC_CLOCK: True,
    }
    flow.hass.config_entries.async_get_known_entry.return_value = mock_entry

    result = await flow.async_step_init()

    assert result["type"] == "form"
    assert result["step_id"] == "init"
    # Voluptuous schema defaults are kept
    schema = result["data_schema"]
    assert schema is not None


@pytest.mark.asyncio
async def test_options_flow_step_init_success() -> None:
    """Test options flow step init succeeds and creates entry on input."""
    flow = JoyonwayP25B85OptionsFlow()
    flow.hass = MagicMock()
    flow.handler = "test_entry"

    mock_entry = MagicMock()
    mock_entry.options = {}
    flow.hass.config_entries.async_get_known_entry.return_value = mock_entry

    user_input = {
        OPT_OZONE_MODE: OZONE_MODE_AUTO,
        OPT_AUTO_SYNC_CLOCK: False,
    }

    result = await flow.async_step_init(user_input)

    assert result["type"] == "create_entry"
    assert result["title"] == ""
    assert result["data"] == {
        OPT_OZONE_MODE: OZONE_MODE_AUTO,
        OPT_AUTO_SYNC_CLOCK: False,
    }
