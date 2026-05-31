"""Switch platform for Joyonway P25B85 — light toggle and pump control.

Uses replay-only command frames captured from the PB554 panel.
No CRC computation — only verbatim captured frames are sent.
"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.p25b85 import CMD_LIGHT_TOGGLE
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [
        SpaLightSwitch(coordinator, entry),
    ]
    async_add_entities(entities)


class SpaLightSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for spa light (toggle command)."""

    _attr_has_entity_name = True
    _attr_translation_key = "light"
    _attr_icon = "mdi:lightbulb"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the light switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_light_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("light")

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on (toggle if currently off)."""
        if not self.is_on:
            await self._send_toggle()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off (toggle if currently on)."""
        if self.is_on:
            await self._send_toggle()

    async def _send_toggle(self) -> None:
        """Send the light toggle command and refresh state."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_LIGHT_TOGGLE)
        if success:
            # Request a refresh after a short delay to pick up the new state
            await coordinator.async_request_refresh()


