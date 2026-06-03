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

from custom_components.joyonway.adapters.p25b85 import P25B85Adapter
from custom_components.joyonway.const import CONF_HOST
from custom_components.joyonway.fan import SpaJetsFan

# Build real command frames
_adapter = P25B85Adapter()
CMD_JETS_LOW = _adapter.build_jets_command("low")
CMD_JETS_HIGH = _adapter.build_jets_command("high")
CMD_JETS_OFF = _adapter.build_jets_command("off")


class DummyHass:
    @staticmethod
    def async_create_task(coro):
        return asyncio.create_task(coro)


class DummyAdapter:
    """Minimal adapter stub used by the fan entity."""

    @staticmethod
    def get_jets_state(data: dict) -> str:
        return data.get("jets", "off")

    @staticmethod
    def build_jets_command(target: str) -> bytes | None:
        if target == "low":
            return CMD_JETS_LOW
        if target == "high":
            return CMD_JETS_HIGH
        if target == "off":
            return CMD_JETS_OFF
        return None


class DummyIntentQueue:
    """Intent queue stub that fires immediately for testing."""

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def submit(self, group, overrides, build_fn, on_failure=None, verify_fn=None):
        frame = build_fn(overrides, self._coordinator.data)
        if frame is not None:
            asyncio.ensure_future(self._coordinator.async_send_command(frame))


class DummyCoordinator:
    """Minimal coordinator stub."""

    def __init__(self, data: dict) -> None:
        self.data = data
        self.adapter = DummyAdapter()
        self.async_send_command = AsyncMock(return_value=True)
        self.intent_queue = DummyIntentQueue(self)

    @property
    def available(self) -> bool:
        return True


def _make_entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="test_entry", data={CONF_HOST: "127.0.0.1"})


def test_fan_supported_features_include_power_actions() -> None:
    coordinator = DummyCoordinator(data={"jets": "off"})
    entity = SpaJetsFan(coordinator, _make_entry())

    assert entity.supported_features & FanEntityFeature.SET_SPEED
    assert entity.supported_features & FanEntityFeature.TURN_ON
    assert entity.supported_features & FanEntityFeature.TURN_OFF


@pytest.mark.asyncio
async def test_fan_turn_on_and_turn_off_paths() -> None:
    coordinator = DummyCoordinator(data={"jets": "off"})
    entity = SpaJetsFan(coordinator, _make_entry())
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    # off -> low via turn_on default path
    await entity.async_turn_on()
    await asyncio.sleep(0)  # let intent queue task execute
    coordinator.async_send_command.assert_awaited_once_with(CMD_JETS_LOW)
    assert entity._pending_state == "low"

    coordinator.async_send_command.reset_mock()

    # Simulate coordinator update clearing pending state
    entity._pending_state = None
    coordinator.data = {"jets": "high"}

    # high -> off via turn_off direct path
    await entity.async_turn_off()
    await asyncio.sleep(0)  # let intent queue task execute
    coordinator.async_send_command.assert_awaited_once_with(CMD_JETS_OFF)
    assert entity._pending_state == "off"
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_fan_percentage_paths() -> None:
    coordinator = DummyCoordinator(data={"jets": "off"})
    entity = SpaJetsFan(coordinator, _make_entry())
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    # Initial percentage
    assert entity.percentage == 0

    # Set percentage to 50 (low)
    await entity.async_set_percentage(50)
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(CMD_JETS_LOW)
    assert entity._pending_state == "low"
    assert entity.percentage == 50

    coordinator.async_send_command.reset_mock()
    entity._pending_state = None
    coordinator.data = {"jets": "low"}

    # Set percentage to 100 (high)
    cmd_high = _adapter.build_jets_command("high")
    await entity.async_set_percentage(100)
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(cmd_high)
    assert entity._pending_state == "high"
    assert entity.percentage == 100

    coordinator.async_send_command.reset_mock()
    entity._pending_state = None
    coordinator.data = {"jets": "high"}

    # Set percentage to 0 (off)
    await entity.async_set_percentage(0)
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(CMD_JETS_OFF)
    assert entity._pending_state == "off"
    assert entity.percentage == 0
    entity._cancel_pending_timeout()
