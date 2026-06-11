"""Shared entity helpers for the Joyonway spa integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, DEFAULT_MODEL, DOMAIN

if TYPE_CHECKING:
    from .coordinator import JoyonwayCoordinator  # noqa: F401


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Build shared device info for entities."""
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=f"Joyonway {model}",
        manufacturer="Joyonway",
        model=model,
        configuration_url=f"http://{entry.data[CONF_HOST]}",
    )


class JoyonwayCoordinatorEntity(CoordinatorEntity["JoyonwayCoordinator"]):
    """Base entity that reads availability from coordinator grace logic."""

    @property
    def available(self) -> bool:
        """Return availability from coordinator (includes grace window)."""
        return self.coordinator.available
