"""Button platform for Joyonway P25B85 — sync spa clock.

Sends the current HA time to the spa controller via a DateTime set command.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaSyncClockButton(coordinator, entry)])


class SpaSyncClockButton(JoyonwayCoordinatorEntity, ButtonEntity):
    """Button entity to sync the spa clock to the current HA time."""

    _attr_has_entity_name = True
    _attr_translation_key = "sync_clock"
    _attr_icon = "mdi:clock-check"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sync clock button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_sync_clock"
        self._attr_device_info = device_info(entry)
        self._cmd_lock = asyncio.Lock()

    async def async_press(self) -> None:
        """Send the current time to the spa controller via intent queue."""
        if self._cmd_lock.locked():
            return  # already in-flight

        async with self._cmd_lock:
            now = dt_util.now()
            coordinator = self.coordinator

            def _build_clock(overrides: dict, data: dict | None) -> bytes | None:
                return coordinator.adapter.build_datetime_command(
                    year=overrides["year"],
                    month=overrides["month"],
                    day=overrides["day"],
                    hour=overrides["hour"],
                    minute=overrides["minute"],
                    second=overrides["second"],
                )

            def _on_failure() -> None:
                _LOGGER.error("Clock sync: command failed")

            _LOGGER.debug("Clock sync: submitting intent %s", now.strftime("%Y-%m-%d %H:%M:%S"))
            coordinator.intent_queue.submit(
                group="clock_sync",
                overrides={
                    "year": now.year,
                    "month": now.month,
                    "day": now.day,
                    "hour": now.hour,
                    "minute": now.minute,
                    "second": now.second,
                },
                build_fn=_build_clock,
                on_failure=_on_failure,
            )
