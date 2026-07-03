"""Shared entity helpers for the Joyonway spa integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, DEFAULT_MODEL, DOMAIN

if TYPE_CHECKING:
    from .coordinator import JoyonwayCoordinator  # noqa: F401


def validate_schedule_data(data: dict | None, schedule_type: str) -> None:
    """Raise HomeAssistantError if schedule prerequisite data is missing."""
    if data is None:
        raise HomeAssistantError("No data available from spa")

    prefix = schedule_type
    required_keys = [
        f"{prefix}_slot1_start",
        f"{prefix}_slot1_end",
        f"{prefix}_slot2_start",
        f"{prefix}_slot2_end",
        f"{prefix}_slot1_enabled",
        f"{prefix}_slot2_enabled",
    ]
    missing = [k for k in required_keys if k not in data]
    if missing:
        raise HomeAssistantError(
            f"Cannot send schedule: missing data keys {missing}. "
            "Wait for the spa to report a full broadcast."
        )


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
