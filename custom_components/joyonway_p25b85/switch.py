"""Switch platform for Joyonway P25B85 — light, heater, blower, ozone, schedule enables.

All command frames are built dynamically via CRC computation.
Writable switches use optimistic state for instant UI feedback.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OPT_OZONE_MODE, OZONE_MODE_MANUAL, OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import JoyonwayP25B85Coordinator
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SwitchEntity] = [
        SpaHeaterSwitch(coordinator, entry),
        SpaLightSwitch(coordinator, entry),
        SpaOzoneSwitch(coordinator, entry),
        SpaBlowerSwitch(coordinator, entry),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 2),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 2),
    ]
    async_add_entities(entities)


class SpaLightSwitch(JoyonwayCoordinatorEntity, SwitchEntity):
    """Switch entity for spa light (toggle command).

    Uses toggle-lock guard: double-clicks are ignored while a toggle is in-flight.
    """

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
        self._pending_state: bool | None = None
        self._cmd_lock = asyncio.Lock()
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when real broadcast arrives."""
        self._cancel_pending_timeout()
        self._pending_state = None
        super()._handle_coordinator_update()

    def _set_pending_state(self, value: bool) -> None:
        self._pending_state = value
        self._arm_pending_timeout()
        self.async_write_ha_state()

    def _arm_pending_timeout(self) -> None:
        self._cancel_pending_timeout()
        self._pending_task = self.hass.async_create_task(self._pending_timeout())

    def _cancel_pending_timeout(self) -> None:
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None

    async def _pending_timeout(self) -> None:
        await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
        self._pending_state = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on."""
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("light")

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on (toggle if currently off)."""
        if self._cmd_lock.locked():
            return  # toggle already in-flight
        state = self.is_on
        if state is None:
            raise HomeAssistantError(
                "Light state is unknown; retry after the next broadcast"
            )
        if not state:
            await self._send_toggle(target=True)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off (toggle if currently on)."""
        if self._cmd_lock.locked():
            return  # toggle already in-flight
        state = self.is_on
        if state is None:
            raise HomeAssistantError(
                "Light state is unknown; retry after the next broadcast"
            )
        if state:
            await self._send_toggle(target=False)

    async def _send_toggle(self, target: bool) -> None:
        """Send the light toggle command with optimistic state."""
        async with self._cmd_lock:
            coordinator: JoyonwayP25B85Coordinator = self.coordinator
            self._set_pending_state(target)
            cmd = coordinator.adapter.build_light_toggle_command()
            _LOGGER.debug("Light: sending toggle command")
            success = await coordinator.async_send_command(cmd)
            if not success:
                self._pending_state = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()
                _LOGGER.error("Light: toggle command failed")
                raise HomeAssistantError("Failed to send light command")


class _SpaTargetStateSwitch(JoyonwayCoordinatorEntity, SwitchEntity):
    """Base class for target-state switches with optimistic UI."""

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._pending_state: bool | None = None
        self._cmd_lock = asyncio.Lock()
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        self._cancel_pending_timeout()
        self._pending_state = None
        super()._handle_coordinator_update()

    def _set_pending_state(self, value: bool) -> None:
        self._pending_state = value
        self._arm_pending_timeout()
        self.async_write_ha_state()

    def _arm_pending_timeout(self) -> None:
        self._cancel_pending_timeout()
        self._pending_task = self.hass.async_create_task(self._pending_timeout())

    def _cancel_pending_timeout(self) -> None:
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None

    async def _pending_timeout(self) -> None:
        await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
        self._pending_state = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    async def _send_target_command(
        self, on: bool, build_cmd, label: str
    ) -> None:
        """Send a target-state command with optimistic state."""
        async with self._cmd_lock:
            self._set_pending_state(on)
            coordinator: JoyonwayP25B85Coordinator = self.coordinator
            cmd = build_cmd(on=on)
            _LOGGER.debug("%s: sending %s command", label, "ON" if on else "OFF")
            success = await coordinator.async_send_command(cmd)
            if not success:
                self._pending_state = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()
                _LOGGER.error("%s: %s command failed", label, "ON" if on else "OFF")
                raise HomeAssistantError(
                    f"Failed to send {label} {'ON' if on else 'OFF'} command"
                )


class SpaHeaterSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa heater (manual ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "heater"
    _attr_icon = "mdi:fire"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_heater_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        status = self.coordinator.data.get("status")
        if status is None:
            return None
        return status in ("circulation", "heating")

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        await self._send_target_command(
            True, self.coordinator.adapter.build_heater_command, "Heater"
        )

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_target_command(
            False, self.coordinator.adapter.build_heater_command, "Heater"
        )


class SpaBlowerSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa blower (air blower ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "blower"
    _attr_icon = "mdi:chart-bubble"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_blower_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("blower")

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        await self._send_target_command(
            True, self.coordinator.adapter.build_blower_command, "Blower"
        )

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_target_command(
            False, self.coordinator.adapter.build_blower_command, "Blower"
        )


class SpaOzoneSwitch(_SpaTargetStateSwitch):
    """Switch entity for ozone control (manual ON/OFF).

    Only available when ozone mode is set to Manual.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "ozone"
    _attr_icon = "mdi:shield-sun"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_filter_switch"
        self._attr_device_info = device_info(entry)
        self._entry = entry

    @property
    def available(self) -> bool:
        """Only available when ozone mode is Manual."""
        if self.coordinator.ozone_mode != OZONE_MODE_MANUAL:
            return False
        return super().available

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("ozone_active")

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        await self._send_target_command(
            True, self.coordinator.adapter.build_ozone_manual_command, "Ozone"
        )

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_target_command(
            False, self.coordinator.adapter.build_ozone_manual_command, "Ozone"
        )


class SpaScheduleSlotSwitch(_SpaTargetStateSwitch):
    """Switch entity to enable/disable a schedule time slot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
        schedule_type: str,
        slot: int,
    ) -> None:
        super().__init__(coordinator, entry)
        self._schedule_type = schedule_type
        self._slot = slot
        self._key = f"{schedule_type}_slot{slot}_enabled"
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = self._key
        self._attr_icon = "mdi:calendar-check" if schedule_type == "heat" else "mdi:air-filter"

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        await self._send_schedule(enabled=True)

    async def async_turn_off(self, **kwargs) -> None:
        if self.is_on is False:
            return
        await self._send_schedule(enabled=False)

    async def _send_schedule(self, enabled: bool) -> None:
        """Send the full schedule command with the slot's enable flag toggled."""
        data = self.coordinator.data
        if data is None:
            raise HomeAssistantError("No data available from spa")

        prefix = self._schedule_type

        required_keys = [
            f"{prefix}_slot1_start",
            f"{prefix}_slot1_end",
            f"{prefix}_slot2_start",
            f"{prefix}_slot2_end",
            f"{prefix}_slot1_enabled",
            f"{prefix}_slot2_enabled",
        ]
        missing = [k for k in required_keys if k not in data]
        if missing:
            raise HomeAssistantError(
                f"Cannot send schedule: missing data keys {missing}. "
                f"Wait for the spa to report a full broadcast before toggling."
            )

        s1_start = data[f"{prefix}_slot1_start"]
        s1_end = data[f"{prefix}_slot1_end"]
        s2_start = data[f"{prefix}_slot2_start"]
        s2_end = data[f"{prefix}_slot2_end"]
        s1_enabled = data[f"{prefix}_slot1_enabled"]
        s2_enabled = data[f"{prefix}_slot2_enabled"]

        if self._slot == 1:
            s1_enabled = enabled
        else:
            s2_enabled = enabled

        adapter = self.coordinator.adapter
        frame = adapter.build_schedule_command(
            self._schedule_type, s1_start, s1_end, s2_start, s2_end,
            slot1_enabled=s1_enabled, slot2_enabled=s2_enabled,
        )

        async with self._cmd_lock:
            self._set_pending_state(enabled)
            _LOGGER.debug(
                "Schedule %s slot %d: sending enable=%s",
                self._schedule_type, self._slot, enabled,
            )
            coordinator: JoyonwayP25B85Coordinator = self.coordinator
            success = await coordinator.async_send_command(frame)
            if not success:
                self._pending_state = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()
                _LOGGER.error(
                    "Schedule %s slot %d: command failed",
                    self._schedule_type, self._slot,
                )
                raise HomeAssistantError(
                    f"Failed to send {self._schedule_type} schedule command"
                )
