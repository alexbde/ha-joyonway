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
    """Test config flow step user transitions to confirmation step on valid connection."""
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

        assert result["type"] == "form"
        assert result["step_id"] == "model_confirm"
        assert flow._host == "127.0.0.1"
        assert flow._port == 8899
        assert flow._detected_model == "P25B85"
        assert result["description_placeholders"]["model"] == "P25B85"
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


@pytest.mark.asyncio
async def test_detect_model_p23b32() -> None:
    """Test connection helper when TCP connection succeeds and signature is P23B32."""
    mock_reader = MagicMock()
    mock_reader.read = AsyncMock(
        return_value=bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02, 0x1D])
    )
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection", return_value=(mock_reader, mock_writer)
    ) as mock_open:
        result = await _detect_model("127.0.0.1", 8899)
        assert result == "P23B32"
        mock_open.assert_called_once_with("127.0.0.1", 8899)
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_detect_model_unknown_signature() -> None:
    """Test connection helper when connection succeeds but signature is unrecognized."""
    mock_reader = MagicMock()
    mock_reader.read = AsyncMock(
        return_value=bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x09, 0x1D])
    )
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()

    with patch(
        "asyncio.open_connection", return_value=(mock_reader, mock_writer)
    ) as mock_open:
        result = await _detect_model("127.0.0.1", 8899)
        assert result == ""
        mock_open.assert_called_once_with("127.0.0.1", 8899)
        mock_writer.close.assert_called_once()
        mock_writer.wait_closed.assert_awaited_once()


@pytest.mark.asyncio
async def test_config_flow_model_confirm_creates_entry() -> None:
    """Test model confirmation step successfully creates config entry."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    flow._host = "127.0.0.1"
    flow._port = 8899

    user_input = {
        CONF_MODEL: "P25B85",
    }

    result = await flow.async_step_model_confirm(user_input)
    assert result["type"] == "create_entry"
    assert result["title"] == "Joyonway Spa (127.0.0.1)"
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
        CONF_MODEL: "P25B85",
    }


@pytest.mark.asyncio
async def test_config_flow_model_confirm_unknown_signature() -> None:
    """Test model confirmation manual step is shown if signature was unknown."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    flow.async_set_unique_id = AsyncMock(return_value="127.0.0.1:8899")
    flow._abort_if_unique_id_configured = MagicMock()

    user_input = {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
    }

    with patch(
        "custom_components.joyonway.config_flow._detect_model",
        return_value="",
    ) as mock_detect:
        result = await flow.async_step_user(user_input)

        assert result["type"] == "form"
        assert result["step_id"] == "model_confirm_manual"
        assert flow._detected_model is None
        mock_detect.assert_called_once_with("127.0.0.1", 8899)


@pytest.mark.asyncio
async def test_config_flow_model_confirm_manual_creates_entry() -> None:
    """Test manual model confirmation step successfully creates config entry."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    flow._host = "127.0.0.1"
    flow._port = 8899

    user_input = {
        CONF_MODEL: "P23B32",
    }

    result = await flow.async_step_model_confirm_manual(user_input)
    assert result["type"] == "create_entry"
    assert result["title"] == "Joyonway Spa (127.0.0.1)"
    assert result["data"] == {
        CONF_HOST: "127.0.0.1",
        CONF_PORT: 8899,
        CONF_MODEL: "P23B32",
    }


@pytest.mark.asyncio
async def test_reconfigure_flow_shows_current_model() -> None:
    """Test reconfigure flow step shows the form with the current model as default."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    mock_entry = MagicMock()
    mock_entry.data = {CONF_MODEL: "P23B32"}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

    result = await flow.async_step_reconfigure()
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    assert CONF_MODEL in result["data_schema"].schema


@pytest.mark.asyncio
async def test_reconfigure_flow_updates_model() -> None:
    """Test reconfigure flow updates entry data and aborts."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()

    mock_entry = MagicMock()
    mock_entry.data = {CONF_MODEL: "P23B32"}
    flow._get_reconfigure_entry = MagicMock(return_value=mock_entry)

    flow.async_update_reload_and_abort = MagicMock(
        return_value={"type": "abort", "reason": "reconfigure_successful"}
    )

    user_input = {CONF_MODEL: "P25B85"}
    result = await flow.async_step_reconfigure(user_input)

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    flow.async_update_reload_and_abort.assert_called_once_with(
        mock_entry,
        data_updates={CONF_MODEL: "P25B85"},
    )
