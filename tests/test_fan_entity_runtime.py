"""Optional Home Assistant runtime regression tests for the fan entity.

These tests auto-skip when Home Assistant is not installed in the environment.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.components.fan import FanEntityFeature

from custom_components.joyonway_p25b85.adapters.p25b85 import (
    CMD_PUMP_HIGH_TO_OFF,
    CMD_PUMP_OFF_TO_LOW,
)
from custom_components.joyonway_p25b85.const import CONF_HOST
from custom_components.joyonway_p25b85.fan import SpaPumpFan


class DummyAdapter:
    """Minimal adapter stub used by the fan entity."""

    @staticmethod
    def get_pump_state(data: dict) -> str:
        if data.get("pump_high"):
            return "high"
        if data.get("pump_low"):
            return "low"
        return "off"


class DummyCoordinator:
    """Minimal coordinator stub used by `CoordinatorEntity` entities in tests."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.adapter = DummyAdapter()
        self.async_send_command = AsyncMock(return_value=True)
        self.async_request_refresh = AsyncMock()


def _make_entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="test_entry", data={CONF_HOST: "127.0.0.1"})


def test_fan_supported_features_include_power_actions() -> None:
    coordinator = DummyCoordinator(data={"pump_low": False, "pump_high": False})
    entity = SpaPumpFan(coordinator, _make_entry())

    assert entity.supported_features & FanEntityFeature.PRESET_MODE
    assert entity.supported_features & FanEntityFeature.TURN_ON
    assert entity.supported_features & FanEntityFeature.TURN_OFF


def test_fan_turn_on_and_turn_off_paths() -> None:
    coordinator = DummyCoordinator(data={"pump_low": False, "pump_high": False})
    entity = SpaPumpFan(coordinator, _make_entry())

    # off -> low via turn_on default path
    asyncio.run(entity.async_turn_on())
    coordinator.async_send_command.assert_awaited_once_with(CMD_PUMP_OFF_TO_LOW)
    coordinator.async_request_refresh.assert_awaited()

    coordinator.async_send_command.reset_mock()
    coordinator.async_request_refresh.reset_mock()

    # high -> off via turn_off direct path
    coordinator.data = {"pump_low": False, "pump_high": True}
    asyncio.run(entity.async_turn_off())
    coordinator.async_send_command.assert_awaited_once_with(CMD_PUMP_HIGH_TO_OFF)
    coordinator.async_request_refresh.assert_awaited()

