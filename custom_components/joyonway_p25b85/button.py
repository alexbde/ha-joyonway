"""Button platform for Joyonway P25B85 — sync spa clock.

Sends the current HA time to the spa controller via a DateTime set command.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up button entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaSyncClockButton(coordinator, entry)])


class SpaSyncClockButton(CoordinatorEntity, ButtonEntity):
    """Button entity to sync the spa clock to the current HA time."""

    _attr_has_entity_name = True
    _attr_translation_key = "sync_clock"
    _attr_icon = "mdi:clock-check"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sync clock button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_sync_clock"
        self._attr_device_info = device_info(entry)

    async def async_press(self) -> None:
        """Send the current time to the spa controller."""
        now = dt_util.now()

        adapter = self.coordinator.adapter
        frame = adapter.build_datetime_command(
            year=now.year,
            month=now.month,
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second,
        )

        success = await self.coordinator.async_send_command(frame)
        if not success:
            raise HomeAssistantError("Failed to send clock sync command")

        _LOGGER.info("Spa clock synced to %s", now.strftime("%Y-%m-%d %H:%M:%S"))
        await self.coordinator.async_request_refresh()

