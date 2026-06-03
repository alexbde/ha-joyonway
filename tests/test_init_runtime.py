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

from custom_components.joyonway import async_unload_entry


@pytest.mark.asyncio
async def test_async_unload_entry() -> None:
    """Test unloading a config entry."""
    coordinator = SimpleNamespace(
        async_shutdown=AsyncMock()
    )
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_unload_platforms=AsyncMock(return_value=True)
        )
    )
    entry = SimpleNamespace(
        runtime_data=coordinator,
        entry_id="entry_1"
    )

    result = await async_unload_entry(hass, entry)
    assert result is True
    hass.config_entries.async_unload_platforms.assert_awaited_once()
    coordinator.async_shutdown.assert_awaited_once()
