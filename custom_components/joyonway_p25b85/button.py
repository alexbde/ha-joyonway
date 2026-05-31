"""Button platform for Joyonway P25B85 — pump cycle control.

The pump has 3 states (off → low → high → off) and transitions require
sending the correct state-specific command frame. A button entity cycles
through states safely based on the current reported pump state.

Uses replay-only command frames captured from the PB554 panel.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.p25b85 import (
    CMD_PUMP_HIGH_TO_OFF,
    CMD_PUMP_LOW_TO_HIGH,
)
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = [
        SpaPumpCycleButton(coordinator, entry),
        SpaPumpOffButton(coordinator, entry),
    ]
    async_add_entities(entities)


class SpaPumpCycleButton(CoordinatorEntity, ButtonEntity):
    """Button to cycle pump: off → low → high → off."""

    _attr_has_entity_name = True
    _attr_translation_key = "pump_cycle"
    _attr_icon = "mdi:pump"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_pump_cycle"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Cycle the pump to the next state."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        data = coordinator.data
        if data is None:
            _LOGGER.warning("Cannot cycle pump: no data from spa")
            return

        adapter = coordinator.adapter
        cmd = adapter.get_pump_cycle_command(data)
        if cmd is None:
            _LOGGER.warning("Cannot determine pump cycle command")
            return

        success = await coordinator.async_send_command(cmd)
        if success:
            await coordinator.async_request_refresh()


class SpaPumpOffButton(CoordinatorEntity, ButtonEntity):
    """Button to turn pump off regardless of current speed.

    If pump is on high, sends high→off.
    If pump is on low, sends low→high then high→off (two steps).
    If already off, does nothing.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "pump_off"
    _attr_icon = "mdi:pump-off"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_pump_off"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Turn pump off."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        data = coordinator.data
        if data is None:
            _LOGGER.warning("Cannot turn off pump: no data from spa")
            return

        adapter = coordinator.adapter
        pump_state = adapter.get_pump_state(data)

        if pump_state == "off":
            return

        if pump_state == "high":
            await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)
        elif pump_state == "low":
            # Must go low→high→off (no direct low→off command available)
            success = await coordinator.async_send_command(CMD_PUMP_LOW_TO_HIGH)
            if success:
                # Wait for controller to process, then send high→off
                import asyncio
                await asyncio.sleep(1.0)
                await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)

        await coordinator.async_request_refresh()


