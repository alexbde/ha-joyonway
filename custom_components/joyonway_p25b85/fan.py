"""Fan platform for Joyonway P25B85 — pump speed control.

The spa has a single dual-speed pump (off / low / high).
Exposed as a fan entity with preset modes for natural HA integration.

Uses replay-only command frames captured from the PB554 panel.
No CRC computation — only verbatim captured frames are sent.
"""
from __future__ import annotations

import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.p25b85 import (
    CMD_PUMP_HIGH_TO_OFF,
    CMD_PUMP_LOW_TO_HIGH,
    CMD_PUMP_OFF_TO_LOW,
)
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)

PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_MODES = [PRESET_LOW, PRESET_HIGH]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fan entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaPumpFan(coordinator, entry)])


class SpaPumpFan(CoordinatorEntity, FanEntity):
    """Fan entity representing the spa pump (off / low / high)."""

    _attr_has_entity_name = True
    _attr_translation_key = "jets"
    _attr_icon = "mdi:weather-windy"
    _attr_preset_modes = PRESET_MODES
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = 2

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the pump fan."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_jets"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if jets are running (any speed)."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("jets", "off") != "off"

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode (low/high) or None if off."""
        if self.coordinator.data is None:
            return None
        jets = self.coordinator.data.get("jets", "off")
        if jets == "high":
            return PRESET_HIGH
        if jets == "low":
            return PRESET_LOW
        return None

    async def async_turn_on(self, preset_mode: str | None = None, **kwargs) -> None:
        """Turn pump on. Default to low if no preset specified."""
        target = preset_mode or PRESET_LOW
        await self._set_pump(target)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn pump off from any state."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        adapter = coordinator.adapter
        current = adapter.get_jets_state(coordinator.data or {})

        if current == "off":
            return

        if current == "high":
            success = await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)
            if not success:
                raise HomeAssistantError("Failed to send pump high->off command")
        elif current == "low":
            # Must go low→high→off (no direct low→off command)
            success = await coordinator.async_send_command(CMD_PUMP_LOW_TO_HIGH)
            if not success:
                raise HomeAssistantError("Failed to send pump low->high command")

            if await self._refresh_and_get_jets_state() != "high":
                raise HomeAssistantError(
                    "Pump transition low->high not confirmed; refusing high->off step"
                )

            success = await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)
            if not success:
                raise HomeAssistantError("Failed to send pump high->off command")

        await coordinator.async_request_refresh()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set pump to a specific preset mode."""
        await self._set_pump(preset_mode)

    async def _set_pump(self, target: str) -> None:
        """Transition pump to the target state (low or high)."""
        if target not in (PRESET_LOW, PRESET_HIGH):
            _LOGGER.warning("Unsupported preset_mode '%s'", target)
            return

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        adapter = coordinator.adapter
        current = adapter.get_jets_state(coordinator.data or {})

        if current == target:
            return

        if target == PRESET_LOW:
            if current == "off":
                success = await coordinator.async_send_command(CMD_PUMP_OFF_TO_LOW)
                if not success:
                    raise HomeAssistantError("Failed to send pump off->low command")
            elif current == "high":
                # high→off→low (two steps)
                success = await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)
                if not success:
                    raise HomeAssistantError("Failed to send pump high->off command")

                if await self._refresh_and_get_jets_state() != "off":
                    raise HomeAssistantError(
                        "Pump transition high->off not confirmed; refusing off->low step"
                    )

                success = await coordinator.async_send_command(CMD_PUMP_OFF_TO_LOW)
                if not success:
                    raise HomeAssistantError("Failed to send pump off->low command")
        elif target == PRESET_HIGH:
            if current == "off":
                # off→low→high (two steps)
                success = await coordinator.async_send_command(CMD_PUMP_OFF_TO_LOW)
                if not success:
                    raise HomeAssistantError("Failed to send pump off->low command")

                if await self._refresh_and_get_jets_state() != "low":
                    raise HomeAssistantError(
                        "Pump transition off->low not confirmed; refusing low->high step"
                    )

                success = await coordinator.async_send_command(CMD_PUMP_LOW_TO_HIGH)
                if not success:
                    raise HomeAssistantError("Failed to send pump low->high command")
            elif current == "low":
                success = await coordinator.async_send_command(CMD_PUMP_LOW_TO_HIGH)
                if not success:
                    raise HomeAssistantError("Failed to send pump low->high command")

        await coordinator.async_request_refresh()

    async def _refresh_and_get_jets_state(self) -> str:
        """Refresh coordinator data and return the current jets state."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        await coordinator.async_request_refresh()
        return coordinator.adapter.get_jets_state(coordinator.data or {})

