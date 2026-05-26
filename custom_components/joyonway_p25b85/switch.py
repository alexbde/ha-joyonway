"""Switch platform for Joyonway P25B85 — light, heater, blower, schedule enables.

Uses replay-only command frames for light/heater/blower.
Uses dynamic CRC-computed frames for schedule enable/disable.
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
    CMD_PUMP_HIGH_TO_OFF,
    CMD_PUMP_OFF_TO_LOW,
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
        SpaHeaterSwitch(coordinator, entry),
        SpaFilterSwitch(coordinator, entry),
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
        status = self.coordinator.data.get("status")
        if status is None:
            return None
        return status in ("circulation", "heating")

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


class SpaFilterSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for manual filtration (pump low = filtration)."""

    _attr_has_entity_name = True
    _attr_translation_key = "filter"
    _attr_icon = "mdi:air-filter"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the filter switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_filter_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if filtration is running (pump low)."""
        if self.coordinator.data is None:
            return None
        jets = self.coordinator.data.get("jets", "off")
        return jets == "low"

    async def async_turn_on(self, **kwargs) -> None:
        """Start filtration (pump low)."""
        if self.is_on:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_PUMP_OFF_TO_LOW)
        if not success:
            raise HomeAssistantError("Failed to send filtration ON command")
        await coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop filtration (pump off)."""
        if self.is_on is False:
            return
        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(CMD_PUMP_HIGH_TO_OFF)
        if not success:
            raise HomeAssistantError("Failed to send filtration OFF command")
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
        self._last_slot_times: tuple[tuple[int, int], tuple[int, int]] | None = None

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
        """Send the full schedule command with updated slot values.

        Until live testing confirms an explicit enable bit in command payload,
        disable is modeled as 00:00-00:00. To keep the toggle reversible,
        the last non-zero slot times are cached and restored on enable.
        """
        data = self.coordinator.data
        if data is None:
            raise HomeAssistantError("No data available from spa")

        prefix = self._schedule_type
        s1_start = data.get(f"{prefix}_slot1_start", (0, 0))
        s1_end = data.get(f"{prefix}_slot1_end", (0, 0))
        s2_start = data.get(f"{prefix}_slot2_start", (0, 0))
        s2_end = data.get(f"{prefix}_slot2_end", (0, 0))

        if self._slot == 1:
            slot_start, slot_end = s1_start, s1_end
        else:
            slot_start, slot_end = s2_start, s2_end

        if not enabled:
            if slot_start != (0, 0) or slot_end != (0, 0):
                self._last_slot_times = (slot_start, slot_end)
            if self._slot == 1:
                s1_start = (0, 0)
                s1_end = (0, 0)
            else:
                s2_start = (0, 0)
                s2_end = (0, 0)
        elif slot_start == (0, 0) and slot_end == (0, 0):
            if self._last_slot_times is None:
                raise HomeAssistantError(
                    "Cannot enable slot with unknown times; set start/end times first"
                )
            if self._slot == 1:
                s1_start, s1_end = self._last_slot_times
            else:
                s2_start, s2_end = self._last_slot_times

        adapter = self.coordinator.adapter
        frame = adapter.build_schedule_command(
            self._schedule_type, s1_start, s1_end, s2_start, s2_end
        )

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        success = await coordinator.async_send_command(frame)
        if not success:
            raise HomeAssistantError(
                f"Failed to send {self._schedule_type} schedule command"
            )
        await coordinator.async_request_refresh()
