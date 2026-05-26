"""Optional Home Assistant runtime tests for entity behavior.

These tests focus on entity logic and service behavior with lightweight stubs.
They auto-skip when Home Assistant is not installed.
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

from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.components.fan import FanEntityFeature
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.exceptions import HomeAssistantError

from custom_components.joyonway_p25b85.adapters.base import SpaEntityDescription
from custom_components.joyonway_p25b85.adapters.p25b85 import (
    CMD_BLOWER_OFF,
    CMD_BLOWER_ON,
    CMD_HEATER_OFF,
    CMD_HEATER_ON,
    CMD_LIGHT_TOGGLE,
)
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
)


class DummyAdapter:
    """Small adapter stub used by several entities."""

    @staticmethod
    def get_jets_state(data: dict) -> str:
        return data.get("jets", "off")

    @staticmethod
    def get_temp_command(target_celsius: int) -> bytes | None:
        return b"\xAA" if 10 <= target_celsius <= 40 else None


class DummyCoordinator:
    """Coordinator stub with async command and refresh APIs."""

    def __init__(self, data: dict, available: bool = True) -> None:
        self.data = data
        self.available = available
        self.adapter = DummyAdapter()
        self.async_send_command = AsyncMock(return_value=True)
        self.async_request_refresh = AsyncMock()


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

    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_light_switch_sends_toggle(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"light": False})
    entity = SpaLightSwitch(coordinator, entry)

    await entity.async_turn_on()

    coordinator.async_send_command.assert_awaited_once_with(CMD_LIGHT_TOGGLE)
    coordinator.async_request_refresh.assert_awaited()


@pytest.mark.asyncio
async def test_heater_and_blower_switch_commands(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"status": "off", "blower": False})
    heater = SpaHeaterSwitch(coordinator, entry)
    blower = SpaBlowerSwitch(coordinator, entry)

    await heater.async_turn_on()
    await blower.async_turn_on()
    coordinator.data["blower"] = True
    await blower.async_turn_off()
    coordinator.data["status"] = "heating"
    await heater.async_turn_off()

    sent = [call.args[0] for call in coordinator.async_send_command.await_args_list]
    assert CMD_HEATER_ON in sent
    assert CMD_BLOWER_ON in sent
    assert CMD_BLOWER_OFF in sent
    assert CMD_HEATER_OFF in sent


def test_fan_reports_power_features(entry: SimpleNamespace) -> None:
    coordinator = DummyCoordinator(data={"jets": "off"})
    fan = SpaPumpFan(coordinator, entry)

    assert fan.supported_features & FanEntityFeature.PRESET_MODE
    assert fan.supported_features & FanEntityFeature.TURN_ON
    assert fan.supported_features & FanEntityFeature.TURN_OFF


def test_climate_action_mapping(entry: SimpleNamespace) -> None:
    heating = SpaClimate(DummyCoordinator(data={"status": "heating"}), entry)
    circulation = SpaClimate(DummyCoordinator(data={"status": "circulation"}), entry)
    idle = SpaClimate(DummyCoordinator(data={"status": "off"}), entry)

    assert heating.hvac_action == HVACAction.HEATING
    assert circulation.hvac_action == HVACAction.PREHEATING
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

    import custom_components.joyonway_p25b85.climate as climate_module

    async def _no_delay(_: float) -> None:
        return None

    monkeypatch.setattr(climate_module, "TEMP_DEBOUNCE_SECONDS", 0)
    monkeypatch.setattr(climate_module.asyncio, "sleep", _no_delay)
    monkeypatch.setattr(climate, "async_write_ha_state", lambda: None)

    climate._pending_temp = 32
    climate._debounce_task = asyncio.current_task()
    await climate._debounced_send(32)

    coordinator.async_send_command.assert_awaited_once_with(b"\xAA")
    coordinator.async_request_refresh.assert_awaited()




