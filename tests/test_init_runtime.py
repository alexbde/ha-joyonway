# ruff: noqa: E402
"""Runtime tests for integration entry lifecycle helpers in __init__.py."""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from custom_components.joyonway import async_setup_entry, async_unload_entry
from custom_components.joyonway.const import CONF_MODEL


@pytest.mark.asyncio
async def test_async_unload_entry() -> None:
    """Test unloading a config entry.

    Coordinator shutdown is no longer invoked directly here: it is
    registered via entry.async_on_unload() during setup so that it also
    runs if the entry fails during its first refresh.
    """
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=True)
        )
    )
    entry = SimpleNamespace(entry_id="entry_1")

    result = await async_unload_entry(hass, entry)
    assert result is True
    hass.config_entries.async_unload_platforms.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_registers_shutdown_with_on_unload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coordinator cleanup must be registered before the first refresh.

    If async_config_entry_first_refresh() raises ConfigEntryError (e.g. an
    unsupported board version), Home Assistant still runs any callbacks
    registered via entry.async_on_unload() even though async_unload_entry()
    is never invoked for an entry that never reached the loaded state.
    """
    coordinator = SimpleNamespace(
        async_setup=AsyncMock(),
        async_config_entry_first_refresh=AsyncMock(),
        async_shutdown=AsyncMock(),
    )
    monkeypatch.setattr(
        "custom_components.joyonway.JoyonwayCoordinator",
        lambda *args, **kwargs: coordinator,
    )

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_forward_entry_setups=AsyncMock())
    )
    on_unload_callbacks: list = []
    entry = SimpleNamespace(
        data={"host": "1.2.3.4", "port": 8899, CONF_MODEL: "p25b85"},
        async_on_unload=on_unload_callbacks.append,
    )

    result = await async_setup_entry(hass, entry)

    assert result is True
    assert on_unload_callbacks == [coordinator.async_shutdown]
