"""Shared entity helpers for the Joyonway P25B85 integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST

from .const import CONF_MODEL, DEFAULT_MODEL, DOMAIN


def device_info(entry: ConfigEntry) -> dict:
    """Build shared device info for entities."""
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": f"Joyonway {model}",
        "manufacturer": "Joyonway",
        "model": model,
        "configuration_url": f"http://{entry.data[CONF_HOST]}",
    }

