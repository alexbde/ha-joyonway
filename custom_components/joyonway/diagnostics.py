"""Diagnostics support for Joyonway."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant

from .coordinator import JoyonwayConfigEntry

TO_REDACT = {CONF_HOST, CONF_PORT}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: JoyonwayConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data

    # Redact sensitive values from config entry data
    config_entry_data = async_redact_data(dict(entry.data), TO_REDACT)
    options = dict(entry.options)

    coordinator_data = {}
    if coordinator.data:
        coordinator_data = dict(coordinator.data)
        # Format any datetime objects to string representation for serialization
        for k, v in coordinator_data.items():
            if hasattr(v, "isoformat"):
                coordinator_data[k] = v.isoformat()

    return {
        "config_entry": config_entry_data,
        "options": options,
        "coordinator_data": coordinator_data,
        "connected": coordinator.is_connected,
    }
