"""Optional Home Assistant runtime tests for entity behavior.

These tests focus on entity logic and service behavior with lightweight stubs.
They auto-skip when Home Assistant is not installed.
"""
from __future__ import annotations

import asyncio
from datetime import time as dt_time
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.exceptions import HomeAssistantError

from custom_components.joyonway_p25b85.adapters.base import SpaEntityDescription
from custom_components.joyonway_p25b85.adapters.p25b85 import P25B85Adapter
from custom_components.joyonway_p25b85.binary_sensor import (
    JoyonwayBinarySensor,
    JoyonwayBridgeConnectivity,
)
from custom_components.joyonway_p25b85.climate import SpaClimate
from custom_components.joyonway_p25b85.const import CONF_HOST
from custom_components.joyonway_p25b85.fan import SpaPumpFan
from custom_components.joyonway_p25b85.sensor import JoyonwaySensor
from custom_components.joyonway_p25b85.switch import (
    SpaBlowerSwitch,
    SpaHeaterSwitch,
    SpaLightSwitch,
    SpaScheduleSlotSwitch,
)
from custom_components.joyonway_p25b85.time import SpaScheduleTime

# Build real command frames for assertion
_adapter = P25B85Adapter()
CMD_LIGHT_TOGGLE = _adapter.build_light_toggle_command()
CMD_HEATER_ON = _adapter.build_heater_command(on=True)
CMD_HEATER_OFF = _adapter.build_heater_command(on=False)
CMD_BLOWER_ON = _adapter.build_blower_command(on=True)
CMD_BLOWER_OFF = _adapter.build_blower_command(on=False)
CMD_PUMP_OFF_TO_LOW = _adapter.build_pump_command("low")
CMD_PUMP_HIGH_TO_OFF = _adapter.build_pump_command("off")


class DummyAdapter:
    """Small adapter stub used by several entities."""

    @staticmethod
    def get_jets_state(data: dict) -> str:
        return data.get("jets", "off")

    @staticmethod
    def build_temp_command(target_celsius: int) -> bytes | None:
        return b"\xAA" if 10 <= target_celsius <= 40 else None

    @staticmethod
    def build_light_toggle_command() -> bytes:
        return CMD_LIGHT_TOGGLE

    @staticmethod
    def build_heater_command(on: bool) -> bytes:
        return CMD_HEATER_ON if on else CMD_HEATER_OFF

    @staticmethod
    def build_blower_command(on: bool) -> bytes:
        return CMD_BLOWER_ON if on else CMD_BLOWER_OFF

    @staticmethod
    def build_pump_command(target: str) -> bytes | None:
        if target == "low":
            return CMD_PUMP_OFF_TO_LOW
        if target == "off":
            return CMD_PUMP_HIGH_TO_OFF
        return None


class DummyIntentQueue:
    """Intent queue stub that fires immediately for testing."""

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def submit(self, group, overrides, build_fn, on_failure=None):
        frame = build_fn(overrides, self._coordinator.data)
        if frame is not None:
            asyncio.ensure_future(self._coordinator.async_send_command(frame))


class DummyCoordinator:
    """Coordinator stub with persistent connection APIs."""

    def __init__(self, data: dict, available: bool = True) -> None:
        self.data = data
        self._available = available
        self.adapter = DummyAdapter()
        self.async_send_command = AsyncMock(return_value=True)
        self.intent_queue = DummyIntentQueue(self)

    @property
    def available(self) -> bool:
        return self._available


class DummyHass:
    """Minimal Home Assistant stub with async task creation."""

    @staticmethod
    def async_create_task(coro):
        return asyncio.create_task(coro)


@pytest.fixture
def entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_1", data={CONF_HOST: "127.0.0.1"})


def test_sensor_entity_reads_coordinator_data(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"water_temperature": 37}, available=True)
    desc = SpaEntityDescription(
        platform="sensor",
        key="water_temperature",
        name="Water temperature",
        device_class="temperature",
    )
    entity = JoyonwaySensor(coordinator, entry, desc)

    assert entity.native_value == 37
    assert entity.available is True
    assert entity.device_class == SensorDeviceClass.TEMPERATURE


def test_binary_sensor_and_connectivity_sensor(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"heater_active": True}, available=False)
    desc = SpaEntityDescription(
        platform="binary_sensor",
        key="heater_active",
        name="Heater active",
        device_class="heat",
    )
    binary = JoyonwayBinarySensor(coordinator, entry, desc)
    connectivity = JoyonwayBridgeConnectivity(coordinator, entry)

    assert binary.is_on is True
    assert binary.available is False
    assert connectivity.available is True
    assert connectivity.is_on is False


@pytest.mark.asyncio
async def test_light_switch_unknown_state_raises(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={})
    entity = SpaLightSwitch(coordinator, entry)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


@pytest.mark.asyncio
async def test_light_switch_sends_toggle(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"light": False})
    entity = SpaLightSwitch(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on()
    await asyncio.sleep(0)  # let intent queue task execute

    coordinator.async_send_command.assert_awaited_once_with(CMD_LIGHT_TOGGLE)
    assert entity._pending_state is True
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_heater_and_blower_switch_commands(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"status": "off", "blower": False})
    heater = SpaHeaterSwitch(coordinator, entry)
    blower = SpaBlowerSwitch(coordinator, entry)
    heater.hass = DummyHass()
    blower.hass = DummyHass()
    heater.async_write_ha_state = lambda: None
    blower.async_write_ha_state = lambda: None

    await heater.async_turn_on()
    await blower.async_turn_on()
    await asyncio.sleep(0)  # let intent queue tasks execute
    # Simulate coordinator update clearing pending state
    blower._handle_coordinator_update()
    coordinator.data["blower"] = True
    await blower.async_turn_off()
    heater._handle_coordinator_update()
    coordinator.data["status"] = "heating"
    await heater.async_turn_off()
    await asyncio.sleep(0)  # let intent queue tasks execute

    sent = [call.args[0] for call in coordinator.async_send_command.await_args_list]
    assert CMD_HEATER_ON in sent
    assert CMD_BLOWER_ON in sent
    assert CMD_BLOWER_OFF in sent
    assert CMD_HEATER_OFF in sent
    heater._cancel_pending_timeout()
    blower._cancel_pending_timeout()


def test_fan_reports_power_features(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"jets": "off"})
    fan = SpaPumpFan(coordinator, entry)

    assert fan.supported_features & FanEntityFeature.PRESET_MODE
    assert fan.supported_features & FanEntityFeature.TURN_ON
    assert fan.supported_features & FanEntityFeature.TURN_OFF


def test_climate_action_mapping(entry: SimpleNamespace) -> None:
    heating = SpaClimate(DummyCoordinator(data={"status": "heating"}), entry)
    circulation = SpaClimate(DummyCoordinator(data={"status": "circulation"}), entry)
    standby = SpaClimate(DummyCoordinator(data={"status": "standby"}), entry)
    idle = SpaClimate(DummyCoordinator(data={"status": "off"}), entry)

    assert heating.hvac_action == HVACAction.HEATING
    assert circulation.hvac_action == HVACAction.PREHEATING
    assert standby.hvac_action == HVACAction.IDLE
    assert idle.hvac_action == HVACAction.IDLE


@pytest.mark.asyncio
async def test_climate_rejects_unsupported_hvac_mode(entry: SimpleNamespace) -> None:
    climate = SpaClimate(DummyCoordinator(data={"status": "off"}), entry)

    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.OFF)


@pytest.mark.asyncio
async def test_climate_debounced_set_temperature_sends_command(
    entry: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = DummyCoordinator(data={"setpoint": 30, "water_temperature": 29})
    climate = SpaClimate(coordinator, entry)
    climate.hass = DummyHass()

    import custom_components.joyonway_p25b85.climate as climate_module

    monkeypatch.setattr(climate_module, "TEMP_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(climate, "async_write_ha_state", lambda: None)

    climate._pending_temp = 32
    climate._debounce_task = asyncio.current_task()
    await climate._debounced_send(32)
    await asyncio.sleep(0)  # let intent queue task execute

    coordinator.async_send_command.assert_awaited_once_with(b"\xAA")
    # pending_temp stays until broadcast confirms (optimistic behavior)
    assert climate._pending_temp == 32
    climate._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_light_double_click_blocked(entry: SimpleNamespace) -> None:
    """Light toggle-lock guard: second click is ignored while in-flight."""
    coordinator = DummyCoordinator(data={"light": False})
    entity = SpaLightSwitch(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    # Acquire the lock to simulate an in-flight toggle
    await entity._cmd_lock.acquire()
    # This should silently return (not block or raise)
    await entity.async_turn_on()
    entity._cmd_lock.release()

    # No command sent while locked
    coordinator.async_send_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_heater_optimistic_state(entry: SimpleNamespace) -> None:
    """Heater shows optimistic state immediately after command send."""
    coordinator = DummyCoordinator(data={"status": "off"})
    heater = SpaHeaterSwitch(coordinator, entry)
    heater.hass = DummyHass()
    heater.async_write_ha_state = lambda: None

    await heater.async_turn_on()

    assert heater._pending_state is True
    assert heater.is_on is True
    heater._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_fan_optimistic_preset_mode(entry: SimpleNamespace) -> None:
    """Fan shows optimistic preset_mode immediately after command send."""
    coordinator = DummyCoordinator(data={"jets": "off"})
    fan = SpaPumpFan(coordinator, entry)
    fan.hass = DummyHass()
    fan.async_write_ha_state = lambda: None

    await fan.async_turn_on()

    assert fan._pending_state == "low"
    assert fan.is_on is True
    assert fan.preset_mode == "low"
    fan._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_schedule_switch_missing_data_raises(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"heat_slot1_enabled": False})
    entity = SpaScheduleSlotSwitch(coordinator, entry, "heat", 1)

    with pytest.raises(HomeAssistantError, match="missing data keys"):
        await entity.async_turn_on()


@pytest.mark.asyncio
async def test_schedule_time_missing_data_raises(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"heat_slot1_start": (8, 0)})
    entity = SpaScheduleTime(
        coordinator,
        entry,
        "heat_slot1_start",
        "heat",
        1,
        "start",
        "mdi:clock-start",
    )

    with pytest.raises(HomeAssistantError, match="missing data keys"):
        await entity.async_set_value(dt_time(hour=9, minute=30))

