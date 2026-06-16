"""Light platform for Joyonway spa controllers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import OPTIMISTIC_TIMEOUT_SECONDS
from .coordinator import JoyonwayCoordinator, JoyonwayConfigEntry
from .entity import JoyonwayCoordinatorEntity, device_info

_LOGGER = logging.getLogger(__name__)

# Preset colors and their approximate HS values
LIGHT_COLOR_MAP: dict[str, tuple[float, float]] = {
    "red": (0.0, 100.0),
    "yellow": (60.0, 100.0),
    "green": (120.0, 100.0),
    "cyan": (180.0, 100.0),
    "blue": (240.0, 100.0),
    "purple": (300.0, 100.0),
    "white": (0.0, 0.0),
}


def map_hs_to_preset(hs_color: tuple[float, float]) -> str:
    """Map arbitrary HS color from color picker to closest preset."""
    hue, sat = hs_color
    if sat < 15.0:
        return "white"

    presets = {
        "red": 0.0,
        "yellow": 60.0,
        "green": 120.0,
        "cyan": 180.0,
        "blue": 240.0,
        "purple": 300.0,
    }
    best_color = "red"
    min_diff = 360.0
    for name, angle in presets.items():
        diff = abs(hue - angle)
        diff = min(diff, 360.0 - diff)
        if diff < min_diff:
            min_diff = diff
            best_color = name
    return best_color


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JoyonwayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up light entities from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities([JoyonwayLight(coordinator, entry)])


class JoyonwayLight(JoyonwayCoordinatorEntity, LightEntity):
    """Light entity for spa light."""

    _attr_has_entity_name = True
    _attr_translation_key = "light"
    _attr_icon = "mdi:lightbulb"

    def __init__(
        self,
        coordinator: JoyonwayCoordinator,
        entry: JoyonwayConfigEntry,
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_light"
        self._attr_device_info = device_info(entry)
        self._cmd_lock = asyncio.Lock()

        self._pending_state: bool | None = None
        self._pending_color_index: int | None = None
        self._pending_task: asyncio.Task | None = None

        # Determine features based on adapter support
        self._supported_colors = coordinator.adapter.supported_light_colors
        if self._supported_colors:
            self._attr_supported_color_modes = {ColorMode.HS}
            self._attr_supported_features = LightEntityFeature.EFFECT
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_supported_features = LightEntityFeature(0)

    @callback
    def _handle_coordinator_update(self) -> None:
        if (
            self._pending_state is not None or self._pending_color_index is not None
        ) and self.coordinator.data is not None:
            if self._broadcast_confirms_pending():
                self._cancel_pending_timeout()
                self._pending_state = None
                self._pending_color_index = None
        else:
            self._cancel_pending_timeout()
            self._pending_state = None
            self._pending_color_index = None
        super()._handle_coordinator_update()

    def _broadcast_confirms_pending(self) -> bool:
        if self.coordinator.data is None:
            return False

        confirms = True
        if self._pending_state is not None:
            confirms = confirms and (
                self.coordinator.data.get("light") == self._pending_state
            )
        if self._pending_color_index is not None:
            confirms = confirms and (
                self.coordinator.data.get("light_color_index")
                == self._pending_color_index
            )
        return confirms

    def _set_pending_state(
        self, state: bool | None = None, color_index: int | None = None
    ) -> None:
        if state is not None:
            self._pending_state = state
        if color_index is not None:
            self._pending_color_index = color_index
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
            "%s: command not confirmed by spa within %ds, reverting state",
            self._attr_translation_key,
            int(OPTIMISTIC_TIMEOUT_SECONDS),
        )
        self._pending_state = None
        self._pending_color_index = None
        self._pending_task = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        await super().async_will_remove_from_hass()
        self._cancel_pending_timeout()

    def _clear_pending_on_failure(self) -> None:
        """Reset optimistic state after a failed command send."""
        self._pending_state = None
        self._pending_color_index = None
        self._cancel_pending_timeout()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool | None:
        """Return True if the light is on."""
        if self._pending_state is not None:
            return self._pending_state
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("light")

    @property
    def color_mode(self) -> ColorMode | None:
        """Return the color mode of the light."""
        if self._supported_colors:
            return ColorMode.HS
        return ColorMode.ONOFF

    @property
    def hs_color(self) -> tuple[float, float] | None:
        """Return the hs color value."""
        if not self._supported_colors:
            return None

        effect = self.effect
        if effect in LIGHT_COLOR_MAP:
            return LIGHT_COLOR_MAP[effect]
        return None

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of supported effects."""
        if self._supported_colors:
            return self._supported_colors
        return None

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        if not self._supported_colors:
            return None

        if self._pending_color_index is not None:
            from .adapters.p25 import LIGHT_COLOR_INDEX_TO_NAME

            return LIGHT_COLOR_INDEX_TO_NAME.get(self._pending_color_index)

        if self.coordinator.data is None:
            return None

        color_index = self.coordinator.data.get("light_color_index")
        if color_index is not None:
            from .adapters.p25 import LIGHT_COLOR_INDEX_TO_NAME

            return LIGHT_COLOR_INDEX_TO_NAME.get(color_index)

        return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        if self._cmd_lock.locked():
            return

        state = self.is_on
        if state is None:
            raise HomeAssistantError("Light state is unknown; retry later")

        # Determine the target effect and action
        target_effect: str | None = None
        if ATTR_EFFECT in kwargs:
            target_effect = kwargs[ATTR_EFFECT]
            if target_effect not in self._supported_colors:
                raise HomeAssistantError(f"Unsupported effect: {target_effect}")
        elif ATTR_HS_COLOR in kwargs:
            target_effect = map_hs_to_preset(kwargs[ATTR_HS_COLOR])

        async with self._cmd_lock:
            coordinator = self.coordinator

            if target_effect is not None:
                from .adapters.p25 import LIGHT_COLOR_NAME_TO_INDEX

                target_idx = LIGHT_COLOR_NAME_TO_INDEX[target_effect]
                self._set_pending_state(state=True, color_index=target_idx)

                def _build_light_color(
                    overrides: dict, data: dict | None
                ) -> bytes | None:
                    if (
                        data is not None
                        and data.get("light") is True
                        and data.get("light_color_index") == target_idx
                    ):
                        return None
                    return coordinator.adapter.build_light_command(
                        on=True, color=target_effect
                    )

                coordinator.intent_queue.submit(
                    group="light",
                    overrides={"light": True, "light_color_index": target_idx},
                    build_fn=_build_light_color,
                    on_failure=self._clear_pending_on_failure,
                )
            else:
                self._set_pending_state(state=True)

                def _build_light_on(overrides: dict, data: dict | None) -> bytes | None:
                    if data is not None and data.get("light") is True:
                        return None
                    return coordinator.adapter.build_light_command(on=True)

                coordinator.intent_queue.submit(
                    group="light",
                    overrides={"light": True},
                    build_fn=_build_light_on,
                    on_failure=self._clear_pending_on_failure,
                )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._cmd_lock.locked():
            return

        state = self.is_on
        if state is None:
            raise HomeAssistantError("Light state is unknown; retry later")

        async with self._cmd_lock:
            coordinator = self.coordinator
            self._set_pending_state(state=False, color_index=0)

            def _build_light_off(overrides: dict, data: dict | None) -> bytes | None:
                if data is not None and data.get("light") is False:
                    return None
                return coordinator.adapter.build_light_command(on=False)

            coordinator.intent_queue.submit(
                group="light",
                overrides={"light": False, "light_color_index": 0},
                build_fn=_build_light_off,
                on_failure=self._clear_pending_on_failure,
            )
