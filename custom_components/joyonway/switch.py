"""Switch platform for Joyonway spa controllers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OZONE_MODE_MANUAL, OPTIMISTIC_TIMEOUT_SECONDS, OPT_AUTO_SYNC_CLOCK
from .coordinator import (
    IntentBuildError,
    JoyonwayCoordinator,
    JoyonwayConfigEntry,
)
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JoyonwayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch entities from a config entry."""
    coordinator = entry.runtime_data

    entities: list[SwitchEntity] = [
        SpaHeaterSwitch(coordinator, entry),
        SpaLightSwitch(coordinator, entry),
        SpaOzoneSwitch(coordinator, entry),
        SpaBlowerSwitch(coordinator, entry),
        SpaAutoClockSyncSwitch(coordinator, entry),
        SpaManualOzoneSwitch(coordinator, entry),
        SpaManualHeaterSwitch(coordinator, entry),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 2),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 1),
        SpaScheduleSlotSwitch(coordinator, entry, "filter", 2),
    ]
    async_add_entities(entities)


class _SpaTargetStateSwitch(JoyonwayCoordinatorEntity, SwitchEntity):
    """Base class for target-state switches with optimistic UI via intent queue."""

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
    ) -> None:
        super().__init__(coordinator)
        self._pending_state: bool | None = None
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending_state is not None and self.coordinator.data is not None:
            if self._broadcast_confirms_pending():
                self._cancel_pending_timeout()
                self._pending_state = None
            # Otherwise keep pending state until timeout (snap-back on timeout)
        else:
            self._cancel_pending_timeout()
            self._pending_state = None
        super()._handle_coordinator_update()

    def _broadcast_confirms_pending(self) -> bool:
        """Return True if the current broadcast data matches the pending state.

        Subclasses override to provide entity-specific confirmation logic.
        Default: always confirms (clears pending immediately).
        """
        return True

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
        _LOGGER.warning(
            "%s: command not confirmed by spa within %ds, reverting state",
            self._attr_translation_key,
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
        self._pending_state = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    def _clear_pending_on_failure(self) -> None:
        """Reset optimistic state after a failed command send."""
        self._pending_state = None
        self._cancel_pending_timeout()
        self.async_write_ha_state()


class SpaLightSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa light (light ON/OFF command).

    Uses command-lock guard: double-clicks are ignored while a command is in-flight.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "light"
    _attr_icon = "mdi:lightbulb"

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        """Initialize the light switch."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_light_switch"
        self._attr_device_info = device_info(entry)
        self._cmd_lock = asyncio.Lock()

    def _broadcast_confirms_pending(self) -> bool:
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.get("light") == self._pending_state

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on."""
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("light")

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on (send light ON command)."""
        if self._cmd_lock.locked():
            return  # command already in-flight
        state = self.is_on
        if state is None:
            raise HomeAssistantError(
                "Light state is unknown; retry after the next broadcast"
            )
        if not state:
            await self._send_light_intent(target=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off (send light OFF command)."""
        if self._cmd_lock.locked():
            return  # command already in-flight
        state = self.is_on
        if state is None:
            raise HomeAssistantError(
                "Light state is unknown; retry after the next broadcast"
            )
        if state:
            await self._send_light_intent(target=False)

    async def _send_light_intent(self, target: bool) -> None:
        """Send the light command intent via intent queue."""
        async with self._cmd_lock:
            coordinator: JoyonwayCoordinator = self.coordinator
            self._set_pending_state(target)

            def _build_light(overrides: dict, data: dict | None) -> bytes | None:
                # Intent coalescing: if the current state already matches the target,
                # then this light command intent is a no-op.
                if data is not None and data.get("light") == overrides.get("light"):
                    return None
                return coordinator.adapter.build_light_command(on=target)

            _LOGGER.debug("Light: submitting light intent (target=%s)", target)
            coordinator.intent_queue.submit(
                group="light",
                overrides={"light": target},
                build_fn=_build_light,
                on_failure=self._clear_pending_on_failure,
            )


class SpaHeaterSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa heater (manual ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "heater"
    _attr_icon = "mdi:fire"

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_heater_switch"
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        """Only available when manual heating is ON."""
        return super().available and self.coordinator.heater_mode == "manual"

    def _get_coordinator_heater_state(self) -> bool | None:
        return self.coordinator.adapter.is_heater_enabled(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        return self._get_coordinator_heater_state()

    def _broadcast_confirms_pending(self) -> bool:
        return self._get_coordinator_heater_state() == self._pending_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._submit_heater_intent(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._submit_heater_intent(on=False)

    def _submit_heater_intent(self, on: bool) -> None:
        """Submit heater intent to the queue."""
        self._set_pending_state(on)
        coordinator = self.coordinator

        def _build_heater(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["heater_enabled"]
            current = coordinator.adapter.is_heater_enabled(data)
            if current == target:
                return None  # no-op
            return coordinator.adapter.build_heater_command(on=target)

        _LOGGER.debug("Heater: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="heater",
            overrides={"heater_enabled": on},
            build_fn=_build_heater,
            on_failure=self._clear_pending_on_failure,
        )


class SpaBlowerSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa blower (air blower ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "blower"
    _attr_icon = "mdi:chart-bubble"

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_blower_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("blower")

    def _broadcast_confirms_pending(self) -> bool:
        return self.coordinator.data.get("blower") == self._pending_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._submit_blower_intent(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._submit_blower_intent(on=False)

    def _submit_blower_intent(self, on: bool) -> None:
        """Submit blower intent to the queue."""
        self._set_pending_state(on)
        coordinator = self.coordinator

        def _build_blower(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["blower"]
            if data is not None and data.get("blower") == target:
                return None  # no-op
            return coordinator.adapter.build_blower_command(on=target)

        _LOGGER.debug("Blower: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="blower",
            overrides={"blower": on},
            build_fn=_build_blower,
            on_failure=self._clear_pending_on_failure,
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
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ozone_switch"
        self._attr_device_info = device_info(entry)

    @property
    def available(self) -> bool:
        """Only available when ozone mode is Manual."""
        return super().available and self.coordinator.ozone_mode == OZONE_MODE_MANUAL

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("ozone_active")

    def _broadcast_confirms_pending(self) -> bool:
        return self.coordinator.data.get("ozone_active") == self._pending_state

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._submit_ozone_intent(on=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._submit_ozone_intent(on=False)

    def _submit_ozone_intent(self, on: bool) -> None:
        """Submit ozone intent to the queue."""
        self._set_pending_state(on)
        coordinator = self.coordinator

        def _build_ozone(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["ozone_active"]
            if data is not None and data.get("ozone_active") == target:
                return None  # no-op
            return coordinator.adapter.build_ozone_manual_command(on=target)

        _LOGGER.debug("Ozone: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="ozone",
            overrides={"ozone_active": on},
            build_fn=_build_ozone,
            on_failure=self._clear_pending_on_failure,
        )


class SpaScheduleSlotSwitch(_SpaTargetStateSwitch):
    """Switch entity to enable/disable a schedule time slot."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
        schedule_type: str,
        slot: int,
    ) -> None:
        super().__init__(coordinator)
        self._schedule_type = schedule_type
        self._slot = slot
        self._key = f"{schedule_type}_slot{slot}_enabled"
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = self._key
        self._attr_icon = (
            "mdi:calendar-check" if schedule_type == "heat" else "mdi:air-filter"
        )

    def _broadcast_confirms_pending(self) -> bool:
        return self.coordinator.data.get(self._key) == self._pending_state

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._validate_schedule_data_available()
        self._submit_schedule_intent(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._validate_schedule_data_available()
        self._submit_schedule_intent(enabled=False)

    def _validate_schedule_data_available(self) -> None:
        """Raise explicit error when schedule payload prerequisites are missing."""
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
                "Wait for the spa to report a full broadcast before toggling."
            )

    def _submit_schedule_intent(self, enabled: bool) -> None:
        """Submit schedule enable/disable intent to the queue (coalesces with siblings)."""
        self._set_pending_state(enabled)
        coordinator = self.coordinator
        schedule_type = self._schedule_type

        def _build_schedule_state(overrides: dict, data: dict | None) -> bytes | None:
            if data is None:
                raise IntentBuildError(f"Schedule {schedule_type}: no data available")
            prefix = schedule_type
            required_keys = [
                f"{prefix}_slot1_start",
                f"{prefix}_slot1_end",
                f"{prefix}_slot2_start",
                f"{prefix}_slot2_end",
                f"{prefix}_slot1_enabled",
                f"{prefix}_slot2_enabled",
            ]
            if any(k not in data for k in required_keys):
                missing = [k for k in required_keys if k not in data]
                raise IntentBuildError(
                    f"Schedule {schedule_type}: missing data keys {missing}"
                )

            # Start from current data, apply overrides
            s1_start = data[f"{prefix}_slot1_start"]
            s1_end = data[f"{prefix}_slot1_end"]
            s2_start = data[f"{prefix}_slot2_start"]
            s2_end = data[f"{prefix}_slot2_end"]
            s1_enabled = overrides.get(
                f"{prefix}_slot1_enabled", data[f"{prefix}_slot1_enabled"]
            )
            s2_enabled = overrides.get(
                f"{prefix}_slot2_enabled", data[f"{prefix}_slot2_enabled"]
            )

            # No-op check: do overrides match current state?
            is_noop = True
            for key, value in overrides.items():
                if data.get(key) != value:
                    is_noop = False
                    break
            if is_noop:
                return None

            return coordinator.adapter.build_schedule_command(
                schedule_type,
                s1_start,
                s1_end,
                s2_start,
                s2_end,
                slot1_enabled=s1_enabled,
                slot2_enabled=s2_enabled,
                write_mode="state",
            )

        _LOGGER.debug(
            "Schedule %s slot %d: submitting intent (enabled=%s)",
            self._schedule_type,
            self._slot,
            enabled,
        )
        # Group by schedule type so heat slot 1 + 2 coalesce into one command
        coordinator.intent_queue.submit(
            group=f"{self._schedule_type}_schedule_state",
            overrides={self._key: enabled},
            build_fn=_build_schedule_state,
            on_failure=self._clear_pending_on_failure,
        )


class SpaAutoClockSyncSwitch(JoyonwayCoordinatorEntity, SwitchEntity):
    """Switch entity for enabling/disabling auto clock sync."""

    _attr_has_entity_name = True
    _attr_translation_key = "auto_sync_clock"
    _attr_icon = "mdi:clock-fast"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_auto_sync_clock"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool:
        """Return True if auto clock sync is enabled."""
        return self.coordinator.auto_sync_clock

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable auto clock sync."""
        new_options = {**self.coordinator.entry.options, OPT_AUTO_SYNC_CLOCK: True}
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry, options=new_options
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable auto clock sync."""
        new_options = {**self.coordinator.entry.options, OPT_AUTO_SYNC_CLOCK: False}
        self.hass.config_entries.async_update_entry(
            self.coordinator.entry, options=new_options
        )

    async def async_will_remove_from_hass(self) -> None:
        """Handle removal from Home Assistant."""
        await super().async_will_remove_from_hass()


class SpaManualOzoneSwitch(_SpaTargetStateSwitch):
    """Switch entity for toggling Ozone Mode (Manual vs Auto)."""

    _attr_has_entity_name = True
    _attr_translation_key = "manual_ozone"
    _attr_icon = "mdi:shield-sun"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_manual_ozone_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.ozone_mode == "manual"

    def _broadcast_confirms_pending(self) -> bool:
        expected = "manual" if self._pending_state else "auto"
        return self.coordinator.ozone_mode == expected

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._submit_mode_intent(manual=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._submit_mode_intent(manual=False)

    def _submit_mode_intent(self, manual: bool) -> None:
        self._set_pending_state(manual)
        coordinator = self.coordinator
        mode = "manual" if manual else "auto"

        def _build_mode(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["mode"]
            if data is not None and data.get("ozone_mode") == target:
                return None
            cmd = coordinator.adapter.build_ozone_mode_command(target)
            if not cmd:
                raise IntentBuildError(
                    "Ozone mode configuration not supported on this model"
                )
            return cmd

        coordinator.intent_queue.submit(
            group="ozone_mode",
            overrides={"mode": mode},
            build_fn=_build_mode,
            on_failure=self._clear_pending_on_failure,
        )


class SpaManualHeaterSwitch(_SpaTargetStateSwitch):
    """Switch entity for toggling Heater Mode / Manual Heating (Manual vs Auto)."""

    _attr_has_entity_name = True
    _attr_translation_key = "manual_heating"
    _attr_icon = "mdi:heating-coil"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_manual_heating_switch"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.heater_mode == "manual"

    def _broadcast_confirms_pending(self) -> bool:
        expected = "manual" if self._pending_state else "auto"
        return self.coordinator.heater_mode == expected

    async def async_turn_on(self, **kwargs: Any) -> None:
        if self.is_on:
            return
        self._submit_mode_intent(manual=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        if self.is_on is False:
            return
        self._submit_mode_intent(manual=False)

    def _submit_mode_intent(self, manual: bool) -> None:
        self._set_pending_state(manual)
        coordinator = self.coordinator
        mode = "manual" if manual else "auto"

        def _build_mode(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["mode"]
            if data is not None and data.get("heater_mode") == target:
                return None
            cmd = coordinator.adapter.build_heater_mode_command(target)
            if not cmd:
                raise IntentBuildError(
                    "Heater mode configuration not supported on this model"
                )
            return cmd

        coordinator.intent_queue.submit(
            group="heater_mode",
            overrides={"mode": mode},
            build_fn=_build_mode,
            on_failure=self._clear_pending_on_failure,
        )
