"""Time platform for Joyonway P25B85 — schedule time slot entities.

Exposes heat and filter schedule start/end times as TimeEntity with write support.
When a time is changed, the full schedule command is sent to the spa controller.
Uses optimistic state for instant UI feedback.
Commands are submitted to the coordinator's intent queue for coalescing
with sibling schedule entities.
"""
from __future__ import annotations

import asyncio
from datetime import time
import logging

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import IntentBuildError, JoyonwayP25B85Coordinator
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)

# Schedule time entity definitions: (key, schedule_type, slot, field, icon)
_SCHEDULE_TIME_DEFS = [
    ("heat_slot1_start", "heat", 1, "start", "mdi:clock-start"),
    ("heat_slot1_end", "heat", 1, "end", "mdi:clock-end"),
    ("heat_slot2_start", "heat", 2, "start", "mdi:clock-start"),
    ("heat_slot2_end", "heat", 2, "end", "mdi:clock-end"),
    ("filter_slot1_start", "filter", 1, "start", "mdi:clock-start"),
    ("filter_slot1_end", "filter", 1, "end", "mdi:clock-end"),
    ("filter_slot2_start", "filter", 2, "start", "mdi:clock-start"),
    ("filter_slot2_end", "filter", 2, "end", "mdi:clock-end"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up time entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = entry.runtime_data
    entities = [
        SpaScheduleTime(coordinator, entry, *defn)
        for defn in _SCHEDULE_TIME_DEFS
    ]
    async_add_entities(entities)


class SpaScheduleTime(JoyonwayCoordinatorEntity, TimeEntity):
    """A time entity for a schedule slot start/end time."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
        key: str,
        schedule_type: str,
        slot: int,
        field: str,
        icon: str,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator)
        self._key = key
        self._schedule_type = schedule_type
        self._slot = slot
        self._field = field
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._pending_state: tuple[int, int] | None = None
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._pending_state is not None and self.coordinator.data is not None:
            value = self.coordinator.data.get(self._key)
            if value is not None and (value[0], value[1]) == self._pending_state:
                # Broadcast confirms the new value — clear optimistic state
                self._cancel_pending_timeout()
                self._pending_state = None
            # Otherwise keep pending state until timeout (snap-back on timeout)
        else:
            self._cancel_pending_timeout()
            self._pending_state = None
        super()._handle_coordinator_update()

    def _set_pending_state(self, value: tuple[int, int]) -> None:
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
            "Schedule %s slot %d %s: time not confirmed by spa within %ds, reverting",
            self._schedule_type, self._slot, self._field,
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
        self._pending_state = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    @property
    def native_value(self) -> time | None:
        """Return the current time value from pending or coordinator data."""
        if self._pending_state is not None:
            return time(hour=self._pending_state[0], minute=self._pending_state[1])
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._key)
        if value is None:
            return None
        return time(hour=value[0], minute=value[1])

    async def async_set_value(self, value: time) -> None:
        """Set a new time value via intent queue."""
        self._validate_schedule_data_available()

        new_val = (value.hour, value.minute)
        self._set_pending_state(new_val)
        coordinator = self.coordinator
        schedule_type = self._schedule_type
        key = self._key

        def _build_schedule_time(overrides: dict, data: dict | None) -> bytes | None:
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

            # Start from current data
            s1_start = data[f"{prefix}_slot1_start"]
            s1_end = data[f"{prefix}_slot1_end"]
            s2_start = data[f"{prefix}_slot2_start"]
            s2_end = data[f"{prefix}_slot2_end"]
            s1_enabled = data[f"{prefix}_slot1_enabled"]
            s2_enabled = data[f"{prefix}_slot2_enabled"]

            # Apply time overrides from merged intents
            if f"{prefix}_slot1_start" in overrides:
                s1_start = overrides[f"{prefix}_slot1_start"]
            if f"{prefix}_slot1_end" in overrides:
                s1_end = overrides[f"{prefix}_slot1_end"]
            if f"{prefix}_slot2_start" in overrides:
                s2_start = overrides[f"{prefix}_slot2_start"]
            if f"{prefix}_slot2_end" in overrides:
                s2_end = overrides[f"{prefix}_slot2_end"]

            # No-op check: do all overrides match current data?
            is_noop = True
            for k, v in overrides.items():
                current = data.get(k)
                if isinstance(v, tuple) and isinstance(current, tuple):
                    if (v[0], v[1]) != (current[0], current[1]):
                        is_noop = False
                        break
                elif current != v:
                    is_noop = False
                    break
            if is_noop:
                return None

            return coordinator.adapter.build_schedule_command(
                schedule_type, s1_start, s1_end, s2_start, s2_end,
                slot1_enabled=s1_enabled, slot2_enabled=s2_enabled,
                write_mode="time",
            )

        def _on_failure() -> None:
            self._pending_state = None
            self._cancel_pending_timeout()
            self.async_write_ha_state()

        _LOGGER.debug(
            "Schedule %s slot %d %s: submitting time intent %02d:%02d",
            self._schedule_type, self._slot, self._field,
            value.hour, value.minute,
        )
        # Group by schedule_type + "time" so multiple time changes coalesce
        coordinator.intent_queue.submit(
            group=f"{self._schedule_type}_schedule_time",
            overrides={key: new_val},
            build_fn=_build_schedule_time,
            on_failure=_on_failure,
        )

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
                "Wait for the spa to report a full broadcast before changing times."
            )

