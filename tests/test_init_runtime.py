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

from custom_components.joyonway_p25b85.__init__ import _async_options_updated
from custom_components.joyonway_p25b85.const import (
    OPT_OZONE_MODE,
    OZONE_MODE_AUTO,
    OZONE_MODE_MANUAL,
)


class DummyIntentQueue:
    """Intent queue stub that records submit/flush call order."""

    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.submissions: list[tuple[str, dict, object]] = []

    def submit(self, group, overrides, build_fn, on_failure=None) -> None:
        self._events.append("submit")
        self.submissions.append((group, overrides, build_fn))

    async def flush(self) -> None:
        self._events.append("flush")


@pytest.mark.asyncio
async def test_options_update_flushes_queue_before_reload() -> None:
    """Mode changes submit + flush before config-entry reload."""
    events: list[str] = []
    queue = DummyIntentQueue(events)

    coordinator = SimpleNamespace(
        intent_queue=queue,
        last_detected_ozone_mode=OZONE_MODE_AUTO,
        adapter=SimpleNamespace(build_ozone_mode_command=lambda mode: b"\xAA"),
    )

    async def _reload(_entry_id: str) -> None:
        events.append("reload")

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_reload=AsyncMock(side_effect=_reload))
    )
    entry = SimpleNamespace(
        runtime_data=coordinator,
        options={OPT_OZONE_MODE: OZONE_MODE_MANUAL},
        entry_id="entry_1",
    )

    await _async_options_updated(hass, entry)

    assert events == ["submit", "flush", "reload"]
    group, overrides, build_fn = queue.submissions[0]
    assert group == "ozone_mode"
    assert overrides == {"mode": OZONE_MODE_MANUAL}
    assert build_fn({"mode": OZONE_MODE_MANUAL}, None) == b"\xAA"


@pytest.mark.asyncio
async def test_options_update_skips_submit_when_mode_is_unchanged() -> None:
    """No submit/flush when configured mode already matches detected mode."""
    events: list[str] = []
    queue = DummyIntentQueue(events)

    coordinator = SimpleNamespace(
        intent_queue=queue,
        last_detected_ozone_mode=OZONE_MODE_MANUAL,
        adapter=SimpleNamespace(build_ozone_mode_command=lambda mode: b"\xAA"),
    )

    async def _reload(_entry_id: str) -> None:
        events.append("reload")

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_reload=AsyncMock(side_effect=_reload))
    )
    entry = SimpleNamespace(
        runtime_data=coordinator,
        options={OPT_OZONE_MODE: OZONE_MODE_MANUAL},
        entry_id="entry_1",
    )

    await _async_options_updated(hass, entry)

    assert events == ["reload"]
    assert queue.submissions == []

