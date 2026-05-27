"""Switch platform for Joyonway P25B85 — light, heater, blower, ozone, schedule enables.

All command frames are built dynamically via CRC computation.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)

# Delay between ozone mode switch and manual ON/OFF command
OZONE_MODE_SWITCH_DELAY = 1.5


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [
        SpaHeaterSwitch(coordinator, entry),
        SpaOzoneSwitch(coordinator, entry),
        SpaLightSwitch(coordinator, entry),
        SpaBlowerSwitch(coordinator, entry),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 2),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 2),
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
        cmd = coordinator.adapter.build_light_toggle_command()
        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError("Failed to send light command")
        await coordinator.async_request_refresh()


class SpaHeaterSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for spa heater (manual ON/OFF commands)."""

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
        status = self.coordinator.data.get("status")
        if status is None:
            return None
        return status in ("circulation", "heating")

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the heater on."""
        if self.is_on:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        cmd = coordinator.adapter.build_heater_command(on=True)
        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError("Failed to send heater ON command")
        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the heater off."""
        if self.is_on is False:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        cmd = coordinator.adapter.build_heater_command(on=False)
        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError("Failed to send heater OFF command")
        await coordinator.async_request_refresh()


class SpaBlowerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for spa blower (air blower ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "blower"
    _attr_icon = "mdi:chart-bubble"

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
        cmd = coordinator.adapter.build_blower_command(on=True)
        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError("Failed to send blower ON command")
        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the blower off."""
        if self.is_on is False:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        cmd = coordinator.adapter.build_blower_command(on=False)
        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError("Failed to send blower OFF command")
        await coordinator.async_request_refresh()


class SpaOzoneSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for ozone / disinfection control.

    Two-step ON process:
      1. Set ozone mode to Manual (enables RS485 control)
      2. Send ozone manual ON command
    OFF process:
      1. Send ozone manual OFF command
      2. Switch back to Auto mode (restores schedule control)

    Replaces the former dummy "filter" switch. Uses the same unique_id
    suffix so existing entity registrations are preserved.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ozone"
    _attr_icon = "mdi:shield-sun"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the ozone switch."""
        super().__init__(coordinator)
        # Keep the old unique_id suffix for migration from the dummy "filter" entity
        self._attr_unique_id = f"{entry.entry_id}_filter_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if ozone/disinfection cycle is active."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("disinfection_active")

    async def async_turn_on(self, **kwargs) -> None:
        """Start ozone disinfection (mode→Manual, then manual ON)."""
        if self.is_on:
            return

        adapter = self.coordinator.adapter
        coordinator: JoyonwayP25B85Coordinator = self.coordinator

        # Step 1: Switch to Manual mode
        mode_cmd = adapter.build_ozone_mode_command("manual")
        success = await coordinator.async_send_command(mode_cmd)
        if not success:
            raise HomeAssistantError("Failed to send ozone mode command")

        # Brief delay to let the controller process the mode switch
        await asyncio.sleep(OZONE_MODE_SWITCH_DELAY)

        # Step 2: Send manual ON
        on_cmd = adapter.build_ozone_manual_command(on=True)
        success = await coordinator.async_send_command(on_cmd)
        if not success:
            raise HomeAssistantError("Failed to send ozone ON command")

        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop ozone disinfection (manual OFF, then mode→Auto)."""
        if self.is_on is False:
            return

        adapter = self.coordinator.adapter
        coordinator: JoyonwayP25B85Coordinator = self.coordinator

        # Step 1: Send manual OFF
        off_cmd = adapter.build_ozone_manual_command(on=False)
        success = await coordinator.async_send_command(off_cmd)
        if not success:
            raise HomeAssistantError("Failed to send ozone OFF command")

        # Brief delay before switching back to Auto
        await asyncio.sleep(OZONE_MODE_SWITCH_DELAY)

        # Step 2: Switch back to Auto mode (restore schedule control)
        mode_cmd = adapter.build_ozone_mode_command("auto")
        success = await coordinator.async_send_command(mode_cmd)
        if not success:
            _LOGGER.warning("Failed to switch ozone back to Auto mode")

        await coordinator.async_request_refresh()


class SpaScheduleSlotSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity to enable/disable a schedule time slot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
        schedule_type: str,
        slot: int,
    ) -> None:
        """Initialize the schedule slot switch."""
        super().__init__(coordinator)
        self._schedule_type = schedule_type
        self._slot = slot
        self._key = f"{schedule_type}_slot{slot}_enabled"
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = self._key
        self._attr_icon = "mdi:calendar-check" if schedule_type == "heat" else "mdi:air-filter"

    @property
    def is_on(self) -> bool | None:
        """Return True if the schedule slot is enabled."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)

    async def async_turn_on(self, **kwargs) -> None:
        """Enable the schedule slot."""
        if self.is_on:
            return
        await self._send_schedule(enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        """Disable the schedule slot."""
        if self.is_on is False:
            return
        await self._send_schedule(enabled=False)

    async def _send_schedule(self, enabled: bool) -> None:
        """Send the full schedule command with the slot's enable flag toggled."""
        data = self.coordinator.data
        if data is None:
            raise HomeAssistantError("No data available from spa")

        prefix = self._schedule_type
        s1_start = data.get(f"{prefix}_slot1_start", (0, 0))
        s1_end = data.get(f"{prefix}_slot1_end", (0, 0))
        s2_start = data.get(f"{prefix}_slot2_start", (0, 0))
        s2_end = data.get(f"{prefix}_slot2_end", (0, 0))
        s1_enabled = data.get(f"{prefix}_slot1_enabled", False)
        s2_enabled = data.get(f"{prefix}_slot2_enabled", False)

        if self._slot == 1:
            s1_enabled = enabled
        else:
            s2_enabled = enabled

        adapter = self.coordinator.adapter
        frame = adapter.build_schedule_command(
            self._schedule_type, s1_start, s1_end, s2_start, s2_end,
            slot1_enabled=s1_enabled, slot2_enabled=s2_enabled,
        )

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(frame)
        if not success:
            raise HomeAssistantError(
                f"Failed to send {self._schedule_type} schedule command"
            )
        await coordinator.async_request_refresh()
