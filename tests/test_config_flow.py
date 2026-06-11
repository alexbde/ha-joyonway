# ruff: noqa: E402
"""Tests for the Joyonway config flow and options flow."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.joyonway.config_flow import (
    JoyonwayConfigFlow,
    _detect_model,
)
from custom_components.joyonway.const import (
    CONF_MODEL,
)
from homeassistant.const import CONF_HOST, CONF_PORT


@pytest.mark.asyncio
async def test_detect_model_success() -> None:
    """Test connection helper when TCP connection succeeds."""
    from unittest.mock import AsyncMock

    mock_reader = MagicMock()
    mock_reader.read = AsyncMock(
        return_value=bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03, 0x1D])
    )
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection", return_value=(mock_reader, mock_writer)
    ) as mock_open:
        result = await _detect_model("127.0.0.1", 8899)
        assert result == "P25B85"
        mock_open.assert_called_once_with("127.0.0.1", 8899)
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_model_failure() -> None:
    """Test connection helper when TCP connection fails."""
    with patch(
        "asyncio.open_connection", side_effect=OSError("connection refused")
    ) as mock_open:
        result = await _detect_model("127.0.0.1", 8899)
        assert result is None
        mock_open.assert_called_once_with("127.0.0.1", 8899)


@pytest.mark.asyncio
async def test_detect_model_empty_stream() -> None:
    """Test connection helper when connection succeeds but stream returns no data."""
    mock_reader = MagicMock()
    mock_reader.read = AsyncMock(return_value=b"")
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection", return_value=(mock_reader, mock_writer)
    ) as mock_open:
        result = await _detect_model("127.0.0.1", 8899)
        assert result is None
        mock_open.assert_called_once_with("127.0.0.1", 8899)
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_flow_user_step_init() -> None:
    """Test config flow step user initializes with correct schema."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    result = await flow.async_step_user()
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert not result["errors"]


@pytest.mark.asyncio
async def test_config_flow_user_step_success() -> None:
    """Test config flow step user completes successfully on valid connection."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    # Mock unique ID methods
    flow.async_set_unique_id = AsyncMock(return_value="127.0.0.1:8899")
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
    }

    with patch(
        "custom_components.joyonway.config_flow._detect_model",
        return_value="P25B85",
    ) as mock_test:
        result = await flow.async_step_user(user_input)

        assert result["type"] == "create_entry"
        assert result["title"] == "Joyonway Spa (127.0.0.1)"
        assert result["data"] == {
            CONF_HOST: "127.0.0.1",
            CONF_PORT: 8899,
            CONF_MODEL: "P25B85",
        }
        mock_test.assert_called_once_with("127.0.0.1", 8899)
        flow.async_set_unique_id.assert_called_once_with("127.0.0.1:8899")
        flow._abort_if_unique_id_configured.assert_called_once()


@pytest.mark.asyncio
async def test_config_flow_user_step_cannot_connect() -> None:
    """Test config flow step user returns error on connection failure."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
    }

    with patch(
        "custom_components.joyonway.config_flow._detect_model",
        return_value=None,
    ):
        result = await flow.async_step_user(user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}
