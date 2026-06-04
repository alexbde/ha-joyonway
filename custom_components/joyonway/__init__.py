"""Joyonway P25B85 integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .const import (
    CONF_MODEL,
    DEFAULT_MODEL,
    PLATFORMS,
)
from .coordinator import JoyonwayP25B85Coordinator, JoyonwayConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: JoyonwayConfigEntry) -> bool:
    """Set up Joyonway P25B85 from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)

    coordinator = JoyonwayP25B85Coordinator(hass, host, port, model, entry)
    await coordinator.async_setup()
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: JoyonwayConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()
    return unload_ok
