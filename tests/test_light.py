"""Pytest coverage for JoyonwayLight platform."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest
from types import SimpleNamespace

from homeassistant.components.light import (
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
)
from homeassistant.exceptions import HomeAssistantError

from custom_components.joyonway.light import (
    JoyonwayLight,
    map_hs_to_preset,
)
from custom_components.joyonway.adapters.p25 import P25B37Adapter, P25B85Adapter


class DummyHass:
    """Mock HomeAssistant for async task creation."""

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class DummyCoordinator:
    """Mock coordinator for entities."""

    def __init__(self, data: dict | None = None, adapter=None) -> None:
        self.data = data
        self.adapter = adapter
        self.available = True
        self.async_send_command = AsyncMock(return_value=True)
        self.intent_queue = MagicMock()
        self._on_data_callbacks = []

        # Simulate intent queue submissions immediately
        self.intent_queue.submit = MagicMock(side_effect=self._mock_submit)

    def _mock_submit(self, group, overrides, build_fn, on_failure=None):
        # Call the builder function
        frame = build_fn(overrides, self.data)
        if frame is not None:
            # Run the mock command sending
            asyncio.create_task(self.async_send_command(frame))

    @property
    def is_connected(self) -> bool:
        return True


@pytest.fixture
def entry() -> SimpleNamespace:
    return SimpleNamespace(
        entry_id="mock_entry_id",
        data={"host": "127.0.0.1", "port": 8899, "model": "P25B37"},
    )


def test_map_hs_to_preset() -> None:
    # Test Saturation < 15
    assert map_hs_to_preset((100, 10)) == "white"

    # Test hue presets
    assert map_hs_to_preset((5, 100)) == "red"
    assert map_hs_to_preset((355, 100)) == "red"
    assert map_hs_to_preset((55, 100)) == "yellow"
    assert map_hs_to_preset((115, 100)) == "green"
    assert map_hs_to_preset((175, 100)) == "cyan"
    assert map_hs_to_preset((245, 100)) == "blue"
    assert map_hs_to_preset((295, 100)) == "purple"


@pytest.mark.asyncio
async def test_light_on_off_only_properties(entry) -> None:
    adapter = P25B85Adapter()  # Doesn't support colors
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    entity = JoyonwayLight(coordinator, entry)

    assert entity.color_mode == ColorMode.ONOFF
    assert entity.supported_color_modes == {ColorMode.ONOFF}
    assert entity.effect_list is None
    assert entity.effect is None
    assert entity.hs_color is None
    assert entity.is_on is False


@pytest.mark.asyncio
async def test_light_color_properties(entry) -> None:
    adapter = P25B37Adapter()  # Supports colors
    coordinator = DummyCoordinator(
        data={"light": True, "light_color_index": 2}, adapter=adapter
    )  # Red

    entity = JoyonwayLight(coordinator, entry)

    assert entity.color_mode == ColorMode.HS
    assert entity.supported_color_modes == {ColorMode.HS}
    assert entity.effect_list == [
        "auto",
        "red",
        "green",
        "yellow",
        "blue",
        "purple",
        "cyan",
        "white",
    ]
    assert entity.effect == "red"
    assert entity.hs_color == (0.0, 100.0)
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_light_turn_on_simple(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    entity = JoyonwayLight(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on()
    await asyncio.sleep(0)  # Let queued tasks run

    # Default build_light_command(on=True)
    expected_frame = adapter.build_light_command(on=True)
    coordinator.async_send_command.assert_awaited_once_with(expected_frame)
    assert entity._pending_state is True
    assert entity._pending_color_index is None
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_light_turn_on_effect(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    entity = JoyonwayLight(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on(**{ATTR_EFFECT: "green"})
    await asyncio.sleep(0)

    # build_light_command(on=True, color="green")
    expected_frame = adapter.build_light_command(on=True, color="green")
    coordinator.async_send_command.assert_awaited_once_with(expected_frame)
    assert entity._pending_state is True
    assert entity._pending_color_index == 3
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_light_turn_on_hs_color(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    entity = JoyonwayLight(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    # Blue is 240
    await entity.async_turn_on(**{ATTR_HS_COLOR: (242.0, 100.0)})
    await asyncio.sleep(0)

    # Should map to blue (index 5)
    expected_frame = adapter.build_light_command(on=True, color="blue")
    coordinator.async_send_command.assert_awaited_once_with(expected_frame)
    assert entity._pending_state is True
    assert entity._pending_color_index == 5
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_light_turn_off(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(
        data={"light": True, "light_color_index": 3}, adapter=adapter
    )

    entity = JoyonwayLight(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_off()
    await asyncio.sleep(0)

    expected_frame = adapter.build_light_command(on=False)
    coordinator.async_send_command.assert_awaited_once_with(expected_frame)
    assert entity._pending_state is False
    assert entity._pending_color_index == 0
    entity._cancel_pending_timeout()


@pytest.mark.asyncio
async def test_light_turn_on_invalid_effect(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    entity = JoyonwayLight(coordinator, entry)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on(**{ATTR_EFFECT: "invalid_color"})


@pytest.mark.asyncio
async def test_light_optimistic_revert(entry) -> None:
    adapter = P25B37Adapter()
    coordinator = DummyCoordinator(data={"light": False}, adapter=adapter)

    # Temporarily speed up timeout for test
    import custom_components.joyonway.light as joyonway_light

    joyonway_light.OPTIMISTIC_TIMEOUT_SECONDS = 0.05

    entity = JoyonwayLight(coordinator, entry)
    entity.hass = DummyHass()
    entity.async_write_ha_state = lambda: None

    await entity.async_turn_on(**{ATTR_EFFECT: "red"})
    assert entity._pending_state is True
    assert entity._pending_color_index == 2

    # Wait for timeout
    await asyncio.sleep(0.06)

    assert entity._pending_state is None
    assert entity._pending_color_index is None
