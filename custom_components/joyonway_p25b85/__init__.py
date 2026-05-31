"""Joyonway P25B85 integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_MODEL, CONF_PORT, DEFAULT_MODEL, DOMAIN, PLATFORMS
from .coordinator import JoyonwayP25B85Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Joyonway P25B85 from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)

    coordinator = JoyonwayP25B85Coordinator(hass, host, port, model)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

