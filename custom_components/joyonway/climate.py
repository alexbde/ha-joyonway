"""Climate platform for Joyonway spa controllers — spa thermostat control.

Supports setpoint temperatures from 10°C to 40°C in 1°C steps.
All command frames are built dynamically via CRC computation.

Includes debouncing for the temperature slider: rapid successive
set_temperature calls (e.g., from dragging the slider) are coalesced
into a single command sent after the slider settles.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature, PRECISION_WHOLE
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .adapters.p25 import TEMP_MAX_C, TEMP_MIN_C
from .const import OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import JoyonwayCoordinator, JoyonwayConfigEntry
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)

# Debounce delay for temperature slider (seconds).
# Waits this long after the last set_temperature call before sending.
TEMP_DEBOUNCE_SECONDS = 0.5


class ManualHeatingDisabled(HomeAssistantError):
    """Exception raised when setting HVAC mode without manual heating enabled."""

    def __init__(self) -> None:
        """Initialize the exception."""
        super().__init__(
            "Cannot change HVAC mode while Manual heating is disabled. "
            "Enable the 'Manual heating' switch in the configuration section first.",
            translation_domain="joyonway",
            translation_key="manual_heating_disabled",
        )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JoyonwayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([SpaClimate(coordinator, entry)])


class SpaClimate(JoyonwayCoordinatorEntity, ClimateEntity):
    """Climate entity for spa thermostat (setpoint control via replay frames)."""

    _attr_has_entity_name = True
    _attr_translation_key = "thermostat"
    _attr_icon = "mdi:hot-tub"
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_WHOLE
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(TEMP_MIN_C)
    _attr_max_temp = float(TEMP_MAX_C)
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        """Initialize the spa thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = device_info(entry)
        self._debounce_task: asyncio.Task | None = None
        self._pending_temp: int | None = None
        self._pending_timeout_task: asyncio.Task | None = None
        self._pending_hvac_mode: HVACMode | None = None
        self._pending_hvac_timeout_task: asyncio.Task | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Clear pending states when broadcast confirms them."""
        if self._pending_temp is not None and self.coordinator.data is not None:
            if self.coordinator.data.get("setpoint") == self._pending_temp:
                self._pending_temp = None
                self._cancel_pending_timeout()

        if self._pending_hvac_mode is not None and self.coordinator.data is not None:
            expected_state = self._pending_hvac_mode == HVACMode.HEAT
            if self._get_coordinator_heater_state() == expected_state:
                self._pending_hvac_mode = None
                self._cancel_pending_hvac_timeout()

        super()._handle_coordinator_update()

    def _get_coordinator_heater_state(self) -> bool | None:
        """Return heater enabled/armed state from coordinator."""
        return self.coordinator.adapter.is_heater_enabled(self.coordinator.data)

    def _arm_pending_timeout(self) -> None:
        """Start a timeout to clear pending temp if broadcast never confirms."""
        self._cancel_pending_timeout()
        self._pending_timeout_task = self.hass.async_create_task(
            self._pending_timeout()
        )

    def _cancel_pending_timeout(self) -> None:
        if self._pending_timeout_task is not None:
            self._pending_timeout_task.cancel()
            self._pending_timeout_task = None

    async def _pending_timeout(self) -> None:
        await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
        _LOGGER.warning(
            "Thermostat: setpoint %d°C not confirmed by spa within %ds, reverting",
            self._pending_temp,
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
        self._pending_temp = None
        self._pending_timeout_task = None
        self.async_write_ha_state()

    def _arm_pending_hvac_timeout(self) -> None:
        """Start a timeout to clear pending HVAC mode if broadcast never confirms."""
        self._cancel_pending_hvac_timeout()
        self._pending_hvac_timeout_task = self.hass.async_create_task(
            self._pending_hvac_timeout()
        )

    def _cancel_pending_hvac_timeout(self) -> None:
        if self._pending_hvac_timeout_task is not None:
            self._pending_hvac_timeout_task.cancel()
            self._pending_hvac_timeout_task = None

    async def _pending_hvac_timeout(self) -> None:
        await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
        _LOGGER.warning(
            "Thermostat: HVAC mode %s not confirmed by spa within %ds, reverting",
            self._pending_hvac_mode,
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
        self._pending_hvac_mode = None
        self._pending_hvac_timeout_task = None
        self.async_write_ha_state()

    @property
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("current_temperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the target (setpoint) temperature."""
        if self._pending_temp is not None:
            return float(self._pending_temp)
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("setpoint")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode based on heater enabled state."""
        if self._pending_hvac_mode is not None:
            return self._pending_hvac_mode
        state = self._get_coordinator_heater_state()
        if state is True:
            return HVACMode.HEAT
        if state is False:
            return HVACMode.OFF
        return HVACMode.HEAT  # fallback / default

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action based on heater state and coordinator status.

        Maps controller heater states to HA actions:
          - HVACMode.OFF → OFF
          - heating → HEATING (actively heating water)
          - circulation → PREHEATING (jet running pre/post-heat)
          - ozone → FAN
          - standby / other → IDLE
        """
        if self.coordinator.data is None:
            return None

        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF

        status = self.coordinator.data.get("status")
        if status == "heating":
            return HVACAction.HEATING
        if status == "circulation":
            return HVACAction.PREHEATING
        if status == "ozone":
            return HVACAction.FAN
        return HVACAction.IDLE

    @property
    def extra_state_attributes(self) -> dict | None:
        """Expose detailed heater state as an extra attribute."""
        if self.coordinator.data is None:
            return None
        return {
            "status": self.coordinator.data.get("status"),
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode by enabling/disabling the heater."""
        if hvac_mode not in self._attr_hvac_modes:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")

        if self.coordinator.heater_mode != "manual":
            raise ManualHeatingDisabled()

        if hvac_mode == HVACMode.HEAT:
            self._submit_heater_intent(on=True)
        elif hvac_mode == HVACMode.OFF:
            self._submit_heater_intent(on=False)

    def _submit_heater_intent(self, on: bool) -> None:
        """Submit heater intent to the queue."""
        self._pending_hvac_mode = HVACMode.HEAT if on else HVACMode.OFF
        self._arm_pending_hvac_timeout()
        self.async_write_ha_state()

        coordinator = self.coordinator

        def _build_heater(overrides: dict, data: dict | None) -> bytes | None:
            target = overrides["heater_enabled"]
            current = coordinator.adapter.is_heater_enabled(data)
            if current == target:
                return None  # no-op
            return coordinator.adapter.build_heater_command(on=target)

        def _on_failure() -> None:
            self._pending_hvac_mode = None
            self._cancel_pending_hvac_timeout()
            self.async_write_ha_state()
            _LOGGER.error(
                "Thermostat: heater command failed for HVAC mode %s",
                "HEAT" if on else "OFF",
            )

        _LOGGER.debug("Thermostat: submitting heater intent (on=%s)", on)
        coordinator.intent_queue.submit(
            group="heater",
            overrides={"heater_enabled": on},
            build_fn=_build_heater,
            on_failure=_on_failure,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature with debouncing for slider support.

        When the slider is dragged, this gets called many times rapidly.
        We debounce: wait TEMP_DEBOUNCE_SECONDS after the last call, then
        send only the final value. This prevents flooding the RS485 bus.
        """
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        target_c = int(round(temperature))
        target_c = max(TEMP_MIN_C, min(TEMP_MAX_C, target_c))
        self._pending_temp = target_c
        self.async_write_ha_state()

        # Cancel any existing debounce timer
        await self._async_cancel_debounce_task()

        # Start a new debounce timer
        self._debounce_task = self.hass.async_create_task(
            self._debounced_send(target_c)
        )

    async def _debounced_send(self, scheduled_target: int) -> None:
        """Wait for debounce period, then submit the temperature intent."""
        try:
            await asyncio.sleep(TEMP_DEBOUNCE_SECONDS)
            # If a newer target has been queued, skip this stale send.
            if self._pending_temp != scheduled_target:
                return

            coordinator: JoyonwayCoordinator = self.coordinator

            def _build_temp(overrides: dict, data: dict | None) -> bytes | None:
                target_c = overrides["setpoint"]
                if data is not None and data.get("setpoint") == target_c:
                    return None  # no-op
                cmd = coordinator.adapter.build_temp_command(target_c)
                if cmd is None:
                    _LOGGER.warning(
                        "Thermostat: cannot build command for %d°C", target_c
                    )
                return cmd

            def _on_failure() -> None:
                self._pending_temp = None
                self._cancel_pending_timeout()
                self.async_write_ha_state()
                _LOGGER.error(
                    "Thermostat: setpoint command failed for %d°C",
                    scheduled_target,
                )

            _LOGGER.debug(
                "Thermostat: submitting setpoint intent %d°C", scheduled_target
            )
            self._arm_pending_timeout()
            coordinator.intent_queue.submit(
                group="setpoint",
                overrides={"setpoint": scheduled_target},
                build_fn=_build_temp,
                on_failure=_on_failure,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception("Unexpected error while sending debounced temperature")
        finally:
            if self._debounce_task is asyncio.current_task():
                self._debounce_task = None

    async def _async_cancel_debounce_task(self) -> None:
        """Cancel and await the debounce task to avoid leaked exceptions."""
        if self._debounce_task is None:
            return

        self._debounce_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._debounce_task
        self._debounce_task = None

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending tasks before entity removal."""
        await super().async_will_remove_from_hass()
        await self._async_cancel_debounce_task()
        self._cancel_pending_timeout()
        self._cancel_pending_hvac_timeout()
