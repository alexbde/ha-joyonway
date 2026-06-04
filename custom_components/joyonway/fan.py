"""Fan platform for Joyonway P25B85 — jets speed control.

The spa has a single dual-speed pump (off / low / high).
Exposed as a fan entity with speed percentage control.
Uses optimistic state with snap-back on the next broadcast mismatch.
Commands are submitted to the coordinator's intent queue.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import JoyonwayP25B85Coordinator, JoyonwayConfigEntry
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JoyonwayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fan entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([SpaJetsFan(coordinator, entry)])


class SpaJetsFan(JoyonwayCoordinatorEntity, FanEntity):
    """Fan entity representing the spa jets (off / low / high)."""

    _attr_has_entity_name = True
    _attr_translation_key = "jets"
    _attr_icon = "mdi:weather-windy"
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )
    _attr_speed_count = 2

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        """Initialize the jets fan."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_jets"
        self._attr_device_info = device_info(entry)
        self._pending_state: str | None = None  # "off", "low", "high", or None
        self._pending_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state or trigger intermediate transitions on update."""
        if self._pending_state is not None and self.coordinator.data is not None:
            current = self.coordinator.adapter.get_jets_state(self.coordinator.data)
            if current == self._pending_state:
                # Broadcast confirms the new value — clear optimistic state
                self._cancel_pending_timeout()
                self._pending_state = None
            else:
                # If we are in an intermediate state of a multi-step transition,
                # trigger the next transition step.
                if (current == "high" and self._pending_state == "off") or (
                    current == "off" and self._pending_state == "low"
                ):
                    self._submit_next_jets_step(current, self._pending_state)
        else:
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
        _LOGGER.warning(
            "Jets: command not confirmed by spa within %ds, reverting state",
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
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
    def percentage(self) -> int | None:
        """Return the current speed percentage."""
        state = self._get_jets_state()
        if state == "high":
            return 100
        if state == "low":
            return 50
        return 0

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage of the fan."""
        if percentage == 0:
            await self.async_turn_off()
            return
        target = "low" if percentage <= 50 else "high"
        self._submit_jets_intent(target)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn jets on."""
        del preset_mode, kwargs
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        self._submit_jets_intent("low")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn jets off."""
        del kwargs
        self._submit_jets_intent("off")

    def _submit_jets_intent(self, target: str) -> None:
        """Submit a jets command intent to the queue."""
        if target not in ("off", "low", "high"):
            _LOGGER.warning("Unsupported jets target '%s'", target)
            return

        if self._pending_state == target:
            return

        self._set_pending_state(target)
        current = self.coordinator.adapter.get_jets_state(self.coordinator.data or {})

        # Determine the immediate next physical command we must send
        if current == "low" and target == "off":
            next_step = "high"
        elif current == "high" and target == "low":
            next_step = "off"
        else:
            next_step = target

        self._send_jets_command(next_step)

    def _submit_next_jets_step(self, current: str, target: str) -> None:
        """Submit the next command step in a multi-step transition."""
        if current == "high" and target == "off":
            next_step = "off"
        elif current == "off" and target == "low":
            next_step = "low"
        else:
            return
        self._send_jets_command(next_step)

    def _send_jets_command(self, next_step: str) -> None:
        """Submit the physical jets command to the queue."""
        coordinator = self.coordinator

        def _build_jets(overrides: dict, data: dict | None) -> bytes | None:
            desired = overrides["jets"]
            cmd = coordinator.adapter.build_jets_command(desired)
            if cmd is None:
                _LOGGER.error("No jets command for target '%s'", desired)
            return cmd

        def _on_failure() -> None:
            self._pending_state = None
            self._cancel_pending_timeout()
            self.async_write_ha_state()

        _LOGGER.debug(
            "Jets: sending step command '%s' (target: %s)",
            next_step,
            self._pending_state,
        )
        self._arm_pending_timeout()
        coordinator.intent_queue.submit(
            group="jets",
            overrides={"jets": next_step},
            build_fn=_build_jets,
            on_failure=_on_failure,
        )
