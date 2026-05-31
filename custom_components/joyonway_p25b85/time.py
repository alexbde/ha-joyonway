"""Time platform for Joyonway P25B85 — schedule time slot entities.

Exposes heat and filter schedule start/end times as TimeEntity with write support.
When a time is changed, the full schedule command is sent to the spa controller.
"""
from __future__ import annotations

from datetime import time
import logging

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)

# Schedule time entity definitions: (key_suffix, schedule_type, slot, field, name, icon)
_SCHEDULE_TIME_DEFS = [
    ("heat_slot1_start", "heat", 1, "start", "Heat slot 1 start", "mdi:clock-start"),
    ("heat_slot1_end", "heat", 1, "end", "Heat slot 1 end", "mdi:clock-end"),
    ("heat_slot2_start", "heat", 2, "start", "Heat slot 2 start", "mdi:clock-start"),
    ("heat_slot2_end", "heat", 2, "end", "Heat slot 2 end", "mdi:clock-end"),
    ("filter_slot1_start", "filter", 1, "start", "Filter slot 1 start", "mdi:clock-start"),
    ("filter_slot1_end", "filter", 1, "end", "Filter slot 1 end", "mdi:clock-end"),
    ("filter_slot2_start", "filter", 2, "start", "Filter slot 2 start", "mdi:clock-start"),
    ("filter_slot2_end", "filter", 2, "end", "Filter slot 2 end", "mdi:clock-end"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up time entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        SpaScheduleTime(coordinator, entry, *defn)
        for defn in _SCHEDULE_TIME_DEFS
    ]
    async_add_entities(entities)


class SpaScheduleTime(CoordinatorEntity, TimeEntity):
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
        name: str,
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

    @property
    def native_value(self) -> time | None:
        """Return the current time value from coordinator data."""
        if self.coordinator.data is None:
            return None
        # Data keys are like "heat_slot1_start" → tuple (hour, minute)
        value = self.coordinator.data.get(self._key)
        if value is None:
            return None
        return time(hour=value[0], minute=value[1])

    @property
    def available(self) -> bool:
        """Return True if coordinator has valid data."""
        return self.coordinator.available

    async def async_set_value(self, value: time) -> None:
        """Set a new time value and send the schedule command."""
        data = self.coordinator.data
        if data is None:
            raise HomeAssistantError("No data available from spa")

        # Gather all current slot values for this schedule type
        prefix = self._schedule_type
        s1_start = data.get(f"{prefix}_slot1_start", (0, 0))
        s1_end = data.get(f"{prefix}_slot1_end", (0, 0))
        s2_start = data.get(f"{prefix}_slot2_start", (0, 0))
        s2_end = data.get(f"{prefix}_slot2_end", (0, 0))
        s1_enabled = data.get(f"{prefix}_slot1_enabled", True)
        s2_enabled = data.get(f"{prefix}_slot2_enabled", True)

        # Replace the one being changed
        new_val = (value.hour, value.minute)
        if self._slot == 1 and self._field == "start":
            s1_start = new_val
        elif self._slot == 1 and self._field == "end":
            s1_end = new_val
        elif self._slot == 2 and self._field == "start":
            s2_start = new_val
        elif self._slot == 2 and self._field == "end":
            s2_end = new_val

        # Build and send the schedule command (preserving current enable state)
        adapter = self.coordinator.adapter
        frame = adapter.build_schedule_command(
            self._schedule_type, s1_start, s1_end, s2_start, s2_end,
            slot1_enabled=s1_enabled, slot2_enabled=s2_enabled,
        )

        success = await self.coordinator.async_send_command(frame)
        if not success:
            raise HomeAssistantError(
                f"Failed to send {self._schedule_type} schedule command"
            )
        await self.coordinator.async_request_refresh()

