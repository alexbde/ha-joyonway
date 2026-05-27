"""Fan platform for Joyonway P25B85 — pump speed control.

The spa has a single dual-speed pump (off / low / high).
Exposed as a fan entity with preset modes for natural HA integration.

All command frames are built dynamically via CRC computation.
"""
from __future__ import annotations

import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)

PRESET_LOW = "low"
PRESET_HIGH = "high"
PRESET_MODES = [PRESET_LOW, PRESET_HIGH]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fan entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaPumpFan(coordinator, entry)])


class SpaPumpFan(CoordinatorEntity, FanEntity):
    """Fan entity representing the spa pump (off / low / high)."""

    _attr_has_entity_name = True
    _attr_translation_key = "jets"
    _attr_icon = "mdi:weather-windy"
    _attr_preset_modes = PRESET_MODES
    _attr_supported_features = (
        FanEntityFeature.PRESET_MODE
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = 2

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the pump fan."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_jets"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool | None:
        """Return True if jets are running (any speed)."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("jets", "off") != "off"

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode (low/high) or None if off."""
        if self.coordinator.data is None:
            return None
        jets = self.coordinator.data.get("jets", "off")
        if jets == "high":
            return PRESET_HIGH
        if jets == "low":
            return PRESET_LOW
        return None

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs,
    ) -> None:
        """Turn pump on. Default to low if no preset specified."""
        target = preset_mode or PRESET_LOW
        await self._send_pump_command(target)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn pump off."""
        await self._send_pump_command("off")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set pump to a specific preset mode."""
        await self._send_pump_command(preset_mode)

    async def _send_pump_command(self, target: str) -> None:
        """Send a single pump command for the given target state."""
        if target not in ("off", PRESET_LOW, PRESET_HIGH):
            _LOGGER.warning("Unsupported pump target '%s'", target)
            return

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        adapter = coordinator.adapter
        current = adapter.get_jets_state(coordinator.data or {})

        if current == target:
            return

        cmd = adapter.build_pump_command(current, target)
        if cmd is None:
            raise HomeAssistantError(
                f"No pump command for transition {current}->{target}"
            )

        success = await coordinator.async_send_command(cmd)
        if not success:
            raise HomeAssistantError(
                f"Failed to send pump {current}->{target} command"
            )
        await coordinator.async_request_refresh()


