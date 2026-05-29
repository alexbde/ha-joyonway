"""Fan platform for Joyonway P25B85 — pump speed control.

The spa has a single dual-speed pump (off / low / high).
Exposed as a fan entity with preset modes for natural HA integration.
Uses optimistic state with max 1 bounded retry on mismatch.
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import JoyonwayP25B85Coordinator
from .entity import JoyonwayCoordinatorEntity, device_info

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


class SpaPumpFan(JoyonwayCoordinatorEntity, FanEntity):
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
        self._pending_state: str | None = None  # "off", "low", "high", or None
        self._cmd_lock = asyncio.Lock()
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state when real broadcast arrives."""
        self._cancel_pending_timeout()
        self._pending_state = None
        super()._handle_coordinator_update()

    def _set_pending_state(self, value: str) -> None:
        self._pending_state = value
        self._arm_pending_timeout()
        self.async_write_ha_state()

    def _arm_pending_timeout(self) -> None:
        self._cancel_pending_timeout()
        self._pending_task = self.hass.async_create_task(self._pending_timeout())

    def _cancel_pending_timeout(self) -> None:
        if self._pending_task is not None:
            self._pending_task.cancel()
            self._pending_task = None

    async def _pending_timeout(self) -> None:
        await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
        self._pending_state = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    def _get_jets_state(self) -> str:
        """Return current jets state from pending or coordinator data."""
        if self._pending_state is not None:
            return self._pending_state
        return self.coordinator.adapter.get_jets_state(self.coordinator.data or {})

    @property
    def is_on(self) -> bool | None:
        """Return True if jets are running (any speed)."""
        if self._pending_state is not None:
            return self._pending_state != "off"
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("jets", "off") != "off"

    @property
    def preset_mode(self) -> str | None:
        """Return current preset mode (low/high) or None if off."""
        state = self._get_jets_state()
        if state == "high":
            return PRESET_HIGH
        if state == "low":
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
        """Send a pump command with optimistic state and max 1 bounded retry."""
        if target not in ("off", PRESET_LOW, PRESET_HIGH):
            _LOGGER.warning("Unsupported pump target '%s'", target)
            return

        current = self._get_jets_state()
        if current == target:
            return

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        adapter = coordinator.adapter
        cmd = adapter.build_pump_command(target)
        if cmd is None:
            raise HomeAssistantError(f"No pump command for target '{target}'")

        async with self._cmd_lock:
            self._set_pending_state(target)
            _LOGGER.debug("Jets %s→%s: sending command", current, target)
            success = await coordinator.async_send_command(cmd)
            if not success:
                self._pending_state = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()
                raise HomeAssistantError(
                    f"Failed to send pump command (target={target})"
                )

