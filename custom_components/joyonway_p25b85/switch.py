"""Switch platform for Joyonway P25B85 — light toggle and pump control.

Uses replay-only command frames captured from the PB554 panel.
No CRC computation — only verbatim captured frames are sent.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.p25b85 import (
    CMD_BLOWER_OFF,
    CMD_BLOWER_ON,
    CMD_HEATER_OFF,
    CMD_HEATER_ON,
    CMD_LIGHT_TOGGLE,
)
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [
        SpaLightSwitch(coordinator, entry),
        SpaHeaterSwitch(coordinator, entry),
        SpaBlowerSwitch(coordinator, entry),
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
        state = self.is_on
        if state is None:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                "Light state is unknown; retry after the next coordinator refresh"
            )
        if not state:
            await self._send_toggle()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off (toggle if currently on)."""
        state = self.is_on
        if state is None:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                "Light state is unknown; retry after the next coordinator refresh"
            )
        if state:
            await self._send_toggle()

    async def _send_toggle(self) -> None:
        """Send the light toggle command and refresh state."""
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_LIGHT_TOGGLE)
        if not success:
            raise HomeAssistantError("Failed to send light command")

        # Request a refresh after a short delay to pick up the new state
        await coordinator.async_request_refresh()


class SpaHeaterSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for spa heater (manual ON/OFF commands).

    Uses distinct ON and OFF command frames captured from the PB554 panel.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "heater"
    _attr_icon = "mdi:fire"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the heater switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_heater_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if the heater is active (circulation or heating)."""
        if self.coordinator.data is None:
            return None
        heater_state = self.coordinator.data.get("heater_state")
        if heater_state is None:
            return None
        return heater_state in ("circulation", "heating")

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the heater on."""
        if self.is_on:
            return  # Already on
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_HEATER_ON)
        if not success:
            raise HomeAssistantError("Failed to send heater ON command")
        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the heater off."""
        if self.is_on is False:
            return  # Already off
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_HEATER_OFF)
        if not success:
            raise HomeAssistantError("Failed to send heater OFF command")
        await coordinator.async_request_refresh()


class SpaBlowerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for spa blower (air blower ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "blower"
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the blower switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_blower_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if the blower is active."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("blower")

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the blower on."""
        if self.is_on:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_BLOWER_ON)
        if not success:
            raise HomeAssistantError("Failed to send blower ON command")
        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the blower off."""
        if self.is_on is False:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_BLOWER_OFF)
        if not success:
            raise HomeAssistantError("Failed to send blower OFF command")
        await coordinator.async_request_refresh()
