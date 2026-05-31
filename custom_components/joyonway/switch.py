"""Switch platform for Joyonway P25B85 — light, heater, blower, ozone, schedule enables.

All command frames are built dynamically via CRC computation.
Writable switches use optimistic state for instant UI feedback.
Commands are submitted to the coordinator's intent queue for coalescing
and sequential execution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OZONE_MODE_MANUAL, OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import IntentBuildError, JoyonwayP25B85Coordinator
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
        *([SpaOzoneSwitch(coordinator, entry)] if coordinator.ozone_mode == OZONE_MODE_MANUAL else []),
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
        """Clear optimistic state only when broadcast confirms the new value."""
        if self._pending_state is not None and self.coordinator.data is not None:
            if self.coordinator.data.get("light") == self._pending_state:
                self._cancel_pending_timeout()
                self._pending_state = None
        else:
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
        _LOGGER.warning(
            "Light: command not confirmed by spa within %ds, reverting state",
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
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

    async def async_turn_on(self, **kwargs: Any) -> None:
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

    async def async_turn_off(self, **kwargs: Any) -> None:
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
        """Send the light toggle command via intent queue."""
        async with self._cmd_lock:
            coordinator: JoyonwayP25B85Coordinator = self.coordinator
            self._set_pending_state(target)

            def _build_light(overrides: dict, data: dict | None) -> bytes | None:
                # Light is a toggle — if current state already matches target,
                # the intent is a no-op (user toggled ON→OFF or vice versa)
                if data is not None and data.get("light") == overrides.get("light"):
                    return None
                return coordinator.adapter.build_light_toggle_command()

            def _on_failure() -> None:
                self._pending_state = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()

            _LOGGER.debug("Light: submitting toggle intent (target=%s)", target)
            coordinator.intent_queue.submit(
                group="light",
                overrides={"light": target},
                build_fn=_build_light,
                on_failure=_on_failure,
            )


class _SpaTargetStateSwitch(JoyonwayCoordinatorEntity, SwitchEntity):
    """Base class for target-state switches with optimistic UI via intent queue."""

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
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
        super().__init__(coordinator)
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
        return status in ("standby", "circulation", "heating")

    def _broadcast_confirms_pending(self) -> bool:
        status = self.coordinator.data.get("status")
        if status is None:
            return False
        current_on = status in ("standby", "circulation", "heating")
        return current_on == self._pending_state

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
            target = overrides["heater_on"]
            if data is not None:
                status = data.get("status")
                current_on = status in ("standby", "circulation", "heating") if status else False
                if current_on == target:
                    return None  # no-op
            return coordinator.adapter.build_heater_command(on=target)

        _LOGGER.debug("Heater: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="heater",
            overrides={"heater_on": on},
            build_fn=_build_heater,
            on_failure=self._clear_pending_on_failure,
        )


class SpaBlowerSwitch(_SpaTargetStateSwitch):
    """Switch entity for spa blower (air blower ON/OFF commands)."""

    _attr_has_entity_name = True
    _attr_translation_key = "blower"
    _attr_icon = "mdi:chart-bubble"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
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
            target = overrides["blower_on"]
            if data is not None and data.get("blower") == target:
                return None  # no-op
            return coordinator.adapter.build_blower_command(on=target)

        _LOGGER.debug("Blower: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="blower",
            overrides={"blower_on": on},
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
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_ozone_switch"
        self._attr_device_info = device_info(entry)

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
            target = overrides["ozone_on"]
            if data is not None and data.get("ozone_active") == target:
                return None  # no-op
            return coordinator.adapter.build_ozone_manual_command(on=target)

        _LOGGER.debug("Ozone: submitting intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="ozone",
            overrides={"ozone_on": on},
            build_fn=_build_ozone,
            on_failure=self._clear_pending_on_failure,
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
        super().__init__(coordinator)
        self._schedule_type = schedule_type
        self._slot = slot
        self._key = f"{schedule_type}_slot{slot}_enabled"
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = self._key
        self._attr_icon = "mdi:calendar-check" if schedule_type == "heat" else "mdi:air-filter"

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
            f"{prefix}_slot1_start", f"{prefix}_slot1_end",
            f"{prefix}_slot2_start", f"{prefix}_slot2_end",
            f"{prefix}_slot1_enabled", f"{prefix}_slot2_enabled",
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
                f"{prefix}_slot1_start", f"{prefix}_slot1_end",
                f"{prefix}_slot2_start", f"{prefix}_slot2_end",
                f"{prefix}_slot1_enabled", f"{prefix}_slot2_enabled",
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
                schedule_type, s1_start, s1_end, s2_start, s2_end,
                slot1_enabled=s1_enabled, slot2_enabled=s2_enabled,
                write_mode="state",
            )

        _LOGGER.debug(
            "Schedule %s slot %d: submitting intent (enabled=%s)",
            self._schedule_type, self._slot, enabled,
        )
        # Group by schedule type so heat slot 1 + 2 coalesce into one command
        coordinator.intent_queue.submit(
            group=f"{self._schedule_type}_schedule_state",
            overrides={self._key: enabled},
            build_fn=_build_schedule_state,
            on_failure=self._clear_pending_on_failure,
        )
