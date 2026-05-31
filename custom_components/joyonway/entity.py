"""Shared entity helpers for the Joyonway P25B85 integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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


class JoyonwayCoordinatorEntity(CoordinatorEntity):
    """Base entity that reads availability from coordinator grace logic."""

    @property
    def available(self) -> bool:
        """Return availability from coordinator (includes grace window)."""
        return self.coordinator.available
