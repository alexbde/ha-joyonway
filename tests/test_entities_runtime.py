# ruff: noqa: E402
"""Optional Home Assistant runtime tests for entity behavior.

These tests focus on entity logic and service behavior with lightweight stubs.
They auto-skip when Home Assistant is not installed.
"""

from __future__ import annotations

import asyncio
from datetime import time as dt_time
from pathlib import Path
import sys
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

from custom_components.joyonway.adapters.base import SpaEntityDescription
from custom_components.joyonway.adapters.p25b85 import P25B85Adapter
from custom_components.joyonway.binary_sensor import (
    JoyonwayBinarySensor,
    JoyonwayBridgeConnectivity,
)
from homeassistant.const import CONF_HOST
from custom_components.joyonway.climate import SpaClimate
from custom_components.joyonway.const import (
    OZONE_MODE_AUTO,
    OZONE_MODE_MANUAL,
    OPT_AUTO_SYNC_CLOCK,
)
from custom_components.joyonway.fan import SpaJetsFan
from custom_components.joyonway.sensor import JoyonwaySensor
from custom_components.joyonway.adapters.base import PumpDescription
from custom_components.joyonway.switch import (
    SpaAutoClockSyncSwitch,
    SpaBlowerSwitch,
    SpaHeaterSwitch,
    SpaLightSwitch,
    SpaManualHeaterSwitch,
    SpaManualOzoneSwitch,
    SpaOzoneSwitch,
    SpaScheduleSlotSwitch,
)
from custom_components.joyonway.time import SpaScheduleTime

# Build real command frames for assertion
_adapter = P25B85Adapter()
CMD_LIGHT_TOGGLE = _adapter.build_light_toggle_command()
CMD_HEATER_ON = _adapter.build_heater_command(on=True)
CMD_HEATER_OFF = _adapter.build_heater_command(on=False)
CMD_BLOWER_ON = _adapter.build_blower_command(on=True)
CMD_BLOWER_OFF = _adapter.build_blower_command(on=False)
CMD_JETS_LOW = _adapter.build_jets_command("jets", "low")
CMD_JETS_HIGH = _adapter.build_jets_command("jets", "high")
CMD_JETS_OFF = _adapter.build_jets_command("jets", "off")


class DummyAdapter:
    """Small adapter stub used by several entities."""

    @staticmethod
    def is_heater_enabled(data: dict | None) -> bool | None:
        if data is None:
            return None
        val = data.get("heater_enabled")
        if val is None:
            status = data.get("status")
            if status is not None:
                val = status in ("standby", "circulation", "heating")
        return val

    @staticmethod
    def get_jets_state(data: dict, pump_id: str) -> str:
        return data.get(pump_id, "off")

    @staticmethod
    def build_temp_command(target_celsius: int) -> bytes | None:
        return b"\xaa" if 10 <= target_celsius <= 40 else None

    @staticmethod
    def build_light_toggle_command(on: bool | None = None) -> bytes:
        return CMD_LIGHT_TOGGLE

    @staticmethod
    def build_heater_command(on: bool) -> bytes:
        return CMD_HEATER_ON if on else CMD_HEATER_OFF

    @staticmethod
    def build_blower_command(on: bool) -> bytes:
        return CMD_BLOWER_ON if on else CMD_BLOWER_OFF

    @staticmethod
    def build_jets_command(pump_id: str, target: str) -> bytes | None:
        if target == "off":
            return CMD_JETS_OFF
        if target == "high":
            return CMD_JETS_HIGH
        return CMD_JETS_LOW


class DummyIntentQueue:
    """Intent queue stub that fires immediately for testing."""

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def submit(self, group, overrides, build_fn, on_failure=None, verify_fn=None):
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

    @property
    def heater_mode(self) -> str:
        if hasattr(self, "_heater_mode"):
            return self._heater_mode
        return self.data.get("heater_mode", "manual")

    @heater_mode.setter
    def heater_mode(self, value: str) -> None:
        self._heater_mode = value


class DummyHass:
    """Minimal Home Assistant stub with async task creation."""

    @staticmethod
    def async_create_task(coro):
        return asyncio.create_task(coro)


@pytest.fixture
def entry() -> SimpleNamespace:
    return SimpleNamespace(entry_id="entry_1", data={CONF_HOST: "127.0.0.1"})


def test_sensor_entity_reads_coordinator_data(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"current_temperature": 37}, available=True)
    desc = SpaEntityDescription(
        platform="sensor",
        key="current_temperature",
        name="Current temperature",
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
    fan = SpaJetsFan(coordinator, entry, PumpDescription(id="jets", name="Jets", type="dual"))

    assert fan.supported_features & FanEntityFeature.SET_SPEED
    assert fan.supported_features & FanEntityFeature.TURN_ON
    assert fan.supported_features & FanEntityFeature.TURN_OFF


def test_climate_action_mapping(entry: SimpleNamespace) -> None:
    # Heater enabled (HEAT mode)
    heating = SpaClimate(
        DummyCoordinator(data={"status": "heating", "heater_enabled": True}), entry
    )
    circulation = SpaClimate(
        DummyCoordinator(data={"status": "circulation", "heater_enabled": True}), entry
    )
    ozone = SpaClimate(
        DummyCoordinator(data={"status": "ozone", "heater_enabled": True}), entry
    )
    standby = SpaClimate(
        DummyCoordinator(data={"status": "standby", "heater_enabled": True}), entry
    )

    # Heater disabled (OFF mode)
    heater_off = SpaClimate(
        DummyCoordinator(data={"status": "off", "heater_enabled": False}), entry
    )

    assert heating.hvac_action == HVACAction.HEATING
    assert circulation.hvac_action == HVACAction.PREHEATING
    assert ozone.hvac_action == HVACAction.FAN
    assert standby.hvac_action == HVACAction.IDLE
    assert heater_off.hvac_action == HVACAction.OFF


@pytest.mark.asyncio
async def test_climate_rejects_unsupported_hvac_mode(entry: SimpleNamespace) -> None:
    climate = SpaClimate(DummyCoordinator(data={"status": "off"}), entry)
    climate.hass = DummyHass()

    with pytest.raises(HomeAssistantError):
        await climate.async_set_hvac_mode(HVACMode.COOL)


@pytest.mark.asyncio
async def test_climate_rejects_hvac_mode_in_auto_mode(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"status": "off", "heater_mode": "auto"})
    climate = SpaClimate(coordinator, entry)
    climate.hass = DummyHass()

    with pytest.raises(HomeAssistantError, match="Manual heating is disabled"):
        await climate.async_set_hvac_mode(HVACMode.HEAT)


@pytest.mark.asyncio
async def test_climate_hvac_mode_commands(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"status": "off", "heater_enabled": False})
    climate = SpaClimate(coordinator, entry)
    climate.hass = DummyHass()
    climate.async_write_ha_state = lambda: None

    # 1. Turn HEAT on
    await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate._pending_hvac_mode == HVACMode.HEAT
    assert climate.hvac_mode == HVACMode.HEAT

    # Let the intent queue run
    await asyncio.sleep(0)
    sent = [call.args[0] for call in coordinator.async_send_command.await_args_list]
    assert CMD_HEATER_ON in sent

    # 2. Simulate broadcast update confirming state
    coordinator.data["heater_enabled"] = True
    climate._handle_coordinator_update()
    assert climate._pending_hvac_mode is None
    assert climate.hvac_mode == HVACMode.HEAT

    # 3. Turn HEAT off
    await climate.async_set_hvac_mode(HVACMode.OFF)
    assert climate._pending_hvac_mode == HVACMode.OFF
    assert climate.hvac_mode == HVACMode.OFF

    # Let the intent queue run
    await asyncio.sleep(0)
    sent = [call.args[0] for call in coordinator.async_send_command.await_args_list]
    assert CMD_HEATER_OFF in sent

    # Cleanup
    climate._cancel_pending_hvac_timeout()


@pytest.mark.asyncio
async def test_climate_hvac_mode_optimistic_and_timeout(
    entry: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = DummyCoordinator(data={"status": "off", "heater_enabled": False})
    climate = SpaClimate(coordinator, entry)
    climate.hass = DummyHass()
    climate.async_write_ha_state = lambda: None

    import custom_components.joyonway.climate as climate_module

    monkeypatch.setattr(climate_module, "OPTIMISTIC_TIMEOUT_SECONDS", 0.01)

    await climate.async_set_hvac_mode(HVACMode.HEAT)
    assert climate._pending_hvac_mode == HVACMode.HEAT
    assert climate.hvac_mode == HVACMode.HEAT

    # Wait for the timeout to fire
    await asyncio.sleep(0.02)
    assert climate._pending_hvac_mode is None
    assert climate.hvac_mode == HVACMode.OFF  # reverted


@pytest.mark.asyncio
async def test_climate_debounced_set_temperature_sends_command(
    entry: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = DummyCoordinator(data={"setpoint": 30, "current_temperature": 29})
    climate = SpaClimate(coordinator, entry)
    climate.hass = DummyHass()

    import custom_components.joyonway.climate as climate_module

    monkeypatch.setattr(climate_module, "TEMP_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(climate, "async_write_ha_state", lambda: None)

    climate._pending_temp = 32
    climate._debounce_task = asyncio.current_task()
    await climate._debounced_send(32)
    await asyncio.sleep(0)  # let intent queue task execute

    coordinator.async_send_command.assert_awaited_once_with(b"\xaa")
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
async def test_fan_optimistic_percentage(entry: SimpleNamespace) -> None:
    """Fan shows optimistic percentage immediately after command send."""
    coordinator = DummyCoordinator(data={"jets": "off"})
    fan = SpaJetsFan(coordinator, entry, PumpDescription(id="jets", name="Jets", type="dual"))
    fan.hass = DummyHass()
    fan.async_write_ha_state = lambda: None

    await fan.async_turn_on()

    assert fan._pending_state == "low"
    assert fan.is_on is True
    assert fan.percentage == 50
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


def test_diagnostic_sensors_runtime(entry: SimpleNamespace) -> None:
    """Test hex-formatting of raw byte keys, native unit for frame length, and diagnostic properties."""
    from homeassistant.helpers.entity import EntityCategory

    # 1. Test raw byte hex-formatting
    coordinator = DummyCoordinator(data={"heater_byte_raw": 85}, available=True)
    desc_heater = SpaEntityDescription(
        platform="sensor",
        key="heater_byte_raw",
        name="Heater byte (raw)",
        entity_category="diagnostic",
    )
    entity_heater = JoyonwaySensor(coordinator, entry, desc_heater)
    assert entity_heater.native_value == "0x55"
    assert entity_heater.entity_category == EntityCategory.DIAGNOSTIC
    assert entity_heater.available is True

    # 2. Test native unit of measurement mapping (e.g. bytes)
    desc_length = SpaEntityDescription(
        platform="sensor",
        key="frame_length",
        name="Frame length",
        native_unit="bytes",
        entity_category="diagnostic",
    )
    coordinator2 = DummyCoordinator(data={"frame_length": 61}, available=True)
    entity_length = JoyonwaySensor(coordinator2, entry, desc_length)
    assert entity_length.native_value == 61
    assert entity_length.native_unit_of_measurement == "bytes"
    assert entity_length.entity_category == EntityCategory.DIAGNOSTIC


@pytest.mark.asyncio
async def test_ozone_switch_availability_and_commands(entry: SimpleNamespace) -> None:
    """Test SpaOzoneSwitch available and commands behavior."""
    coordinator = DummyCoordinator(data={"ozone_active": False})
    coordinator.entry = entry
    coordinator.ozone_mode = OZONE_MODE_AUTO

    CMD_OZONE_ON = b"\x55"
    CMD_OZONE_OFF = b"\x66"
    coordinator.adapter.build_ozone_manual_command = lambda on: (
        CMD_OZONE_ON if on else CMD_OZONE_OFF
    )

    entity = SpaOzoneSwitch(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    # Available check (should be false because ozone mode is auto)
    assert entity.available is False

    # Switch ozone mode to manual
    coordinator.ozone_mode = OZONE_MODE_MANUAL
    assert entity.available is True

    # Turn ozone on
    await entity.async_turn_on()
    assert entity._pending_state is True
    assert entity.is_on is True

    # Let the mock intent queue process command
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(CMD_OZONE_ON)

    # Confirm state update
    coordinator.data["ozone_active"] = True
    entity._handle_coordinator_update()
    assert entity._pending_state is None
    assert entity.is_on is True

    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_auto_clock_sync_switch(entry: SimpleNamespace) -> None:
    """Test SpaAutoClockSyncSwitch status and toggling."""
    entry.options = {OPT_AUTO_SYNC_CLOCK: False}
    coordinator = DummyCoordinator(data={})
    coordinator.entry = entry
    coordinator.auto_sync_clock = False

    entity = SpaAutoClockSyncSwitch(coordinator, entry)
    entity.hass = DummyHass()

    # Mock config entry updates
    updated_options = {}

    def mock_update_entry(entry, options):
        updated_options.update(options)
        entry.options = options

    entity.hass.config_entries = SimpleNamespace(async_update_entry=mock_update_entry)

    assert entity.is_on is False

    # Turn on auto clock sync switch
    await entity.async_turn_on()
    assert updated_options[OPT_AUTO_SYNC_CLOCK] is True

    # Turn off auto clock sync switch
    await entity.async_turn_off()
    assert updated_options[OPT_AUTO_SYNC_CLOCK] is False


@pytest.mark.asyncio
async def test_manual_ozone_switch(entry: SimpleNamespace) -> None:
    """Test SpaManualOzoneSwitch toggles ozone mode."""
    coordinator = DummyCoordinator(data={})
    coordinator.entry = entry
    coordinator.ozone_mode = "auto"

    CMD_AUTO = b"\x11"
    CMD_MANUAL = b"\x22"
    coordinator.adapter.build_ozone_mode_command = lambda mode: (
        CMD_AUTO if mode == "auto" else CMD_MANUAL
    )

    entity = SpaManualOzoneSwitch(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    assert entity.is_on is False

    # Turn on manual ozone
    await entity.async_turn_on()
    assert entity._pending_state is True
    assert entity.is_on is True

    # Let mock intent queue run
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(CMD_MANUAL)

    # Confirm update
    coordinator.ozone_mode = "manual"
    entity._handle_coordinator_update()
    assert entity._pending_state is None
    assert entity.is_on is True

    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_manual_heating_switch(entry: SimpleNamespace) -> None:
    """Test SpaManualHeaterSwitch toggles heater mode."""
    coordinator = DummyCoordinator(data={})
    coordinator.entry = entry
    coordinator.heater_mode = "auto"

    CMD_AUTO = b"\x33"
    CMD_MANUAL = b"\x44"
    coordinator.adapter.build_heater_mode_command = lambda mode: (
        CMD_AUTO if mode == "auto" else CMD_MANUAL
    )

    entity = SpaManualHeaterSwitch(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    assert entity.is_on is False

    # Turn on manual heating
    await entity.async_turn_on()
    assert entity._pending_state is True
    assert entity.is_on is True

    # Let mock intent queue run
    await asyncio.sleep(0)
    coordinator.async_send_command.assert_awaited_once_with(CMD_MANUAL)

    # Confirm update
    coordinator.heater_mode = "manual"
    entity._handle_coordinator_update()
    assert entity._pending_state is None
    assert entity.is_on is True

    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_heater_switch_availability(entry: SimpleNamespace) -> None:
    """Test SpaHeaterSwitch availability checks heater_mode."""
    coordinator = DummyCoordinator(data={})
    coordinator.entry = entry
    coordinator.heater_mode = "auto"

    entity = SpaHeaterSwitch(coordinator, entry)

    # In auto mode, should be unavailable
    assert entity.available is False

    # In manual mode, should be available
    coordinator.heater_mode = "manual"
    assert entity.available is True
