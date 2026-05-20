"""Climate platform for Joyonway P25B85 — spa thermostat control.

Uses replay-only command frames captured from the PB554 panel.
No CRC computation — only verbatim captured frames are sent.
Supports setpoint temperatures from 10°C to 40°C in 1°C steps.
"""
from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.p25b85 import TEMP_MAX_C, TEMP_MIN_C
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up climate entities from a config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SpaClimate(coordinator, entry)])


class SpaClimate(CoordinatorEntity, ClimateEntity):
    """Climate entity for spa thermostat (setpoint control via replay frames)."""

    _attr_has_entity_name = True
    _attr_translation_key = "thermostat"
    _attr_icon = "mdi:hot-tub"
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1.0
    _attr_min_temp = float(TEMP_MIN_C)
    _attr_max_temp = float(TEMP_MAX_C)
    _enable_turn_on_off_backwards_compat = False

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the spa thermostat."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = device_info(entry)

    @property
    def current_temperature(self) -> float | None:
        """Return the current water temperature."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("water_temperature")

    @property
    def target_temperature(self) -> float | None:
        """Return the target (setpoint) temperature."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("setpoint")

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode — spa is always in HEAT mode."""
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action based on heater state."""
        if self.coordinator.data is None:
            return None
        if self.coordinator.data.get("heater_active"):
            return HVACAction.HEATING
        return HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode — only HEAT is supported (no-op)."""
        # Spa is always heating to setpoint; nothing to do.

    async def async_set_temperature(self, **kwargs) -> None:
        """Set the target temperature using a captured command frame."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        target_c = int(round(temperature))
        target_c = max(TEMP_MIN_C, min(TEMP_MAX_C, target_c))

        coordinator: JoyonwayP25B85Coordinator = self.coordinator
        adapter = coordinator.adapter
        command = adapter.get_temp_command(target_c)

        if command is None:
            _LOGGER.warning(
                "No captured command frame for %d°C — cannot set temperature",
                target_c,
            )
            return

        success = await coordinator.async_send_command(command)
        if success:
            _LOGGER.debug("Sent temperature command for %d°C", target_c)
            await coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to send temperature command for %d°C", target_c)

