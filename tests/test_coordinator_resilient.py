# ruff: noqa: E402
"""Advanced coordinator tests for the resilient persistent connection model.

Tests cover: reconnect logic, shutdown races, stale-RX detection,
availability grace window, optimistic timeout, and HA lifecycle.
Auto-skip when Home Assistant is not installed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.const import CONF_HOST, CONF_PORT
from custom_components.joyonway.const import (
    AVAILABILITY_GRACE_SECONDS,
    RX_STALE_SECONDS,
)
from custom_components.joyonway.coordinator import (
    IntentBuildError,
    JoyonwayP25B85Coordinator,
)


class FakeHass:
    """Minimal hass stub for coordinator tests."""

    def __init__(self):
        self.config_entries = MagicMock()
        self._tasks: list[asyncio.Task] = []

    def async_create_task(self, coro):
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task


class FakeEntry:
    """Minimal config entry stub."""

    def __init__(self):
        self.entry_id = "test_entry"
        self.data = {CONF_HOST: "127.0.0.1", CONF_PORT: 8899}
        self.options = {}

    def async_on_unload(self, func):
        """Mock async_on_unload."""
        pass


@pytest.fixture
def hass():
    return FakeHass()


@pytest.fixture
def entry():
    return FakeEntry()


@pytest.fixture
def coordinator(hass, entry):
    coord = JoyonwayP25B85Coordinator(hass, "127.0.0.1", 8899, "P25B85", entry)
    coord._sync_timeout = 0.0
    return coord


# ── Shutdown / race condition tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_no_reconnect_after_shutdown(coordinator):
    """After async_shutdown, _schedule_reconnect does nothing."""
    await coordinator.async_shutdown()
    assert coordinator._stopped is True

    coordinator._schedule_reconnect()
    assert coordinator._reconnect_task is None


@pytest.mark.asyncio
async def test_only_one_reconnect_task(coordinator, hass):
    """Multiple _schedule_reconnect calls create only one task."""
    coordinator._reconnect_delay = 100.0  # won't fire during test
    coordinator._schedule_reconnect()
    first_task = coordinator._reconnect_task
    assert first_task is not None

    coordinator._schedule_reconnect()
    assert coordinator._reconnect_task is first_task  # same task, not replaced

    # Cleanup
    first_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_task


@pytest.mark.asyncio
async def test_connect_guards_against_stopped(coordinator):
    """_connect does nothing when _stopped is True."""
    coordinator._stopped = True
    await coordinator._connect()
    assert coordinator._writer is None


@pytest.mark.asyncio
async def test_connect_guards_against_already_connected(coordinator):
    """_connect does nothing when already connected."""
    # Fake an active writer
    coordinator._writer = MagicMock()
    with patch("asyncio.open_connection") as mock_open:
        await coordinator._connect()
        mock_open.assert_not_called()
    coordinator._writer = None  # cleanup


# ── Availability grace window tests ─────────────────────────────────


def test_availability_true_when_connected(coordinator):
    """Available when _available is True."""
    coordinator._available = True
    coordinator.data = {"test": 1}
    assert coordinator.available is True


def test_availability_false_no_data(coordinator):
    """Not available when data is None."""
    coordinator._available = False
    coordinator.data = None
    coordinator._disconnect_ts = time.monotonic()
    assert coordinator.available is False


def test_availability_grace_within_window(coordinator):
    """Available during grace window after disconnect."""
    coordinator._available = False
    coordinator.data = {"test": 1}
    coordinator._disconnect_ts = time.monotonic()  # just disconnected
    assert coordinator.available is True


def test_availability_grace_expired(coordinator):
    """Not available after grace window expires."""
    coordinator._available = False
    coordinator.data = {"test": 1}
    coordinator._disconnect_ts = time.monotonic() - AVAILABILITY_GRACE_SECONDS - 1
    assert coordinator.available is False


# ── Stale-RX health check tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stale_rx_triggers_reconnect(coordinator, hass):
    """Fallback poll reconnects when no RX data for too long."""
    coordinator._available = True
    coordinator.data = {"test": 1}
    coordinator._last_rx_ts = time.monotonic() - RX_STALE_SECONDS - 5

    # Patch _close_connection, _schedule_reconnect, and _connect to avoid real IO
    coordinator._close_connection = AsyncMock()
    coordinator._schedule_reconnect = MagicMock()
    coordinator._connect = AsyncMock()

    result = await coordinator._async_update_data()

    # Stale RX should have triggered close + reconnect
    coordinator._close_connection.assert_awaited_once()
    coordinator._schedule_reconnect.assert_called()
    assert coordinator._available is False
    # _connect was called (from the `if not self._available` branch)
    coordinator._connect.assert_awaited_once()
    # Data is still returned (cached) since it's not None
    assert result == {"test": 1}


# ── Command send tests ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_command_not_connected(coordinator, hass):
    """async_send_command returns False when not connected."""
    coordinator._writer = None
    coordinator._schedule_reconnect = MagicMock()

    result = await coordinator.async_send_command(b"\x00")

    assert result is False
    coordinator._schedule_reconnect.assert_called_once()


@pytest.mark.asyncio
async def test_send_command_success(coordinator):
    """async_send_command writes to socket and returns True."""
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    coordinator._writer = mock_writer

    result = await coordinator.async_send_command(b"\xab\xcd")

    assert result is True
    mock_writer.write.assert_called_once_with(b"\xab\xcd")
    mock_writer.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_command_failure_triggers_reconnect(coordinator, hass):
    """async_send_command triggers reconnect on OSError."""
    mock_writer = MagicMock()
    mock_writer.write = MagicMock(side_effect=OSError("broken"))
    mock_writer.drain = AsyncMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    coordinator._writer = mock_writer
    coordinator._schedule_reconnect = MagicMock()

    result = await coordinator.async_send_command(b"\x00")

    assert result is False
    coordinator._schedule_reconnect.assert_called_once()
    assert coordinator._writer is None  # connection closed


# ── Buffer parsing tests ─────────────────────────────────────────────


def test_parse_buffer_empty(coordinator):
    """Empty buffer returns None and zero consumed."""
    result, consumed = coordinator._try_parse_buffer(b"")
    assert result is None
    assert consumed == 0


def test_parse_buffer_no_broadcast(coordinator):
    """Non-broadcast frames are skipped, bytes still consumed."""
    # A minimal valid frame (not broadcast: byte[1] != 0xFF)
    frame = b"\x1a\x01\x02\x03\x1d"
    result, consumed = coordinator._try_parse_buffer(bytearray(frame))
    assert result is None
    assert consumed == len(frame)


def test_parse_buffer_multiple_broadcasts(coordinator):
    """Multiple broadcast frames in one buffer are parsed, returning the latest valid state."""
    frame1 = b"\x1a\xff\x02\x03\x1d"
    frame2 = b"\x1a\xff\x04\x05\x1d"

    mock_parse = MagicMock(
        side_effect=[{"status": "heating"}, {"status": "circulation"}]
    )
    coordinator._adapter.parse_status = mock_parse
    coordinator._adapter.unescape_full_frame = True

    result, consumed = coordinator._try_parse_buffer(bytearray(frame1 + frame2))
    assert result == {"status": "circulation"}  # the latest parsed broadcast
    assert consumed == len(frame1) + len(frame2)
    assert mock_parse.call_count == 2


def test_parse_buffer_exception_handling(coordinator):
    """Expected exceptions from adapter.parse_status are caught and skipped."""
    frame1 = b"\x1a\xff\x02\x03\x1d"
    frame2 = b"\x1a\xff\x04\x05\x1d"

    # First frame raises IndexError, second succeeds
    mock_parse = MagicMock(side_effect=[IndexError("too short"), {"status": "standby"}])
    coordinator._adapter.parse_status = mock_parse
    coordinator._adapter.unescape_full_frame = True

    result, consumed = coordinator._try_parse_buffer(bytearray(frame1 + frame2))
    assert result == {"status": "standby"}  # first skipped, second returned
    assert consumed == len(frame1) + len(frame2)


# ── Shutdown lifecycle tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_shutdown_cancels_reconnect_task(coordinator, hass):
    """async_shutdown cancels any pending reconnect task."""
    coordinator._reconnect_delay = 100.0
    coordinator._schedule_reconnect()
    task = coordinator._reconnect_task
    assert task is not None

    await coordinator.async_shutdown()

    assert coordinator._stopped is True
    assert coordinator._reconnect_task is None
    # Task cancel was requested
    await asyncio.sleep(0)  # let cancellation propagate
    assert task.done()


@pytest.mark.asyncio
async def test_shutdown_closes_writer(coordinator):
    """async_shutdown properly closes the writer."""
    mock_writer = MagicMock()
    mock_writer.close = MagicMock()
    mock_writer.wait_closed = AsyncMock()
    coordinator._writer = mock_writer

    await coordinator.async_shutdown()

    mock_writer.close.assert_called_once()
    mock_writer.wait_closed.assert_awaited_once()
    assert coordinator._writer is None


@pytest.mark.asyncio
async def test_intent_queue_flush_drains_pending_immediately(coordinator):
    """flush() sends queued intents immediately (without waiting coalesce timer)."""
    coordinator.async_send_command = AsyncMock(return_value=True)

    coordinator.intent_queue.submit(
        group="test_group",
        overrides={"x": 1},
        build_fn=lambda overrides, data: b"\xaa",
        verify_fn=lambda overrides, data: True,
    )
    await coordinator.intent_queue.flush()

    coordinator.async_send_command.assert_awaited_once_with(b"\xaa")


@pytest.mark.asyncio
async def test_intent_queue_build_error_triggers_on_failure(coordinator):
    """IntentBuildError is explicit failure (not silent no-op)."""
    coordinator.async_send_command = AsyncMock(return_value=True)
    failed = {"value": False}

    def _on_failure() -> None:
        failed["value"] = True

    def _build(_overrides, _data):
        raise IntentBuildError("missing schedule keys")

    coordinator.intent_queue.submit(
        group="test_group",
        overrides={"x": 1},
        build_fn=_build,
        on_failure=_on_failure,
    )
    await coordinator.intent_queue.flush()

    assert failed["value"] is True
    coordinator.async_send_command.assert_not_awaited()


# ── Optimistic timeout integration tests ─────────────────────────────


class _DummyIntentQueue:
    """Intent queue stub for testing — executes immediately."""

    def __init__(self, coordinator):
        self._coordinator = coordinator

    def submit(self, group, overrides, build_fn, on_failure=None):
        frame = build_fn(overrides, self._coordinator.data)
        if frame is not None:
            asyncio.ensure_future(self._coordinator.async_send_command(frame))


def _mock_is_heater_enabled(data: dict | None) -> bool | None:
    if data is None:
        return None
    val = data.get("heater_enabled")
    if val is None:
        status = data.get("status")
        if status is not None:
            val = status in ("standby", "circulation", "heating")
    return val


@pytest.mark.asyncio
async def test_optimistic_timeout_clears_pending_state():
    """Pending state auto-clears after timeout."""
    from custom_components.joyonway.switch import SpaHeaterSwitch

    class QuickCoordinator:
        data = {"status": "off"}
        adapter = type(
            "A",
            (),
            {
                "build_heater_command": staticmethod(lambda on: b"\x01"),
                "is_heater_enabled": staticmethod(_mock_is_heater_enabled),
            },
        )()

        @property
        def available(self):
            return True

        async_send_command = AsyncMock(return_value=True)

    coordinator = QuickCoordinator()
    coordinator.intent_queue = _DummyIntentQueue(coordinator)
    entry = SimpleNamespace(entry_id="e1", data={CONF_HOST: "127.0.0.1"})
    heater = SpaHeaterSwitch(coordinator, entry)
    heater.hass = FakeHass()
    heater.async_write_ha_state = MagicMock()

    # Monkeypatch timeout to be very short
    import custom_components.joyonway.switch as switch_mod

    original = switch_mod.OPTIMISTIC_TIMEOUT_SECONDS
    switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = 0.05

    try:
        await heater.async_turn_on()
        assert heater._pending_state is True

        # Wait for timeout to fire
        await asyncio.sleep(0.1)
        assert heater._pending_state is None
    finally:
        switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_optimistic_timeout_canceled_on_coordinator_update():
    """Pending timeout is canceled when _handle_coordinator_update fires."""
    from custom_components.joyonway.switch import SpaHeaterSwitch

    class QuickCoordinator:
        data = {"status": "off"}
        adapter = type(
            "A",
            (),
            {
                "build_heater_command": staticmethod(lambda on: b"\x01"),
                "is_heater_enabled": staticmethod(_mock_is_heater_enabled),
            },
        )()

        @property
        def available(self):
            return True

        async_send_command = AsyncMock(return_value=True)

    coordinator = QuickCoordinator()
    coordinator.intent_queue = _DummyIntentQueue(coordinator)
    entry = SimpleNamespace(entry_id="e1", data={CONF_HOST: "127.0.0.1"})
    heater = SpaHeaterSwitch(coordinator, entry)
    heater.hass = FakeHass()
    heater.async_write_ha_state = MagicMock()

    import custom_components.joyonway.switch as switch_mod

    original = switch_mod.OPTIMISTIC_TIMEOUT_SECONDS
    switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = 5.0  # long timeout

    try:
        await heater.async_turn_on()
        assert heater._pending_state is True
        assert heater._pending_task is not None

        # Simulate broadcast that still shows old state — pending should persist
        heater._handle_coordinator_update()
        assert heater._pending_state is True
        assert heater._pending_task is not None

        # Simulate broadcast confirming the new state
        coordinator.data = {"status": "heating"}
        heater._handle_coordinator_update()
        assert heater._pending_state is None
        assert heater._pending_task is None
    finally:
        switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_pending_timeout_canceled_on_entity_removal():
    """Pending timeout task is canceled on async_will_remove_from_hass."""
    from custom_components.joyonway.switch import SpaHeaterSwitch

    class QuickCoordinator:
        data = {"status": "off"}
        adapter = type(
            "A",
            (),
            {
                "build_heater_command": staticmethod(lambda on: b"\x01"),
                "is_heater_enabled": staticmethod(_mock_is_heater_enabled),
            },
        )()

        @property
        def available(self):
            return True

        async_send_command = AsyncMock(return_value=True)

    coordinator = QuickCoordinator()
    coordinator.intent_queue = _DummyIntentQueue(coordinator)
    entry = SimpleNamespace(entry_id="e1", data={CONF_HOST: "127.0.0.1"})
    heater = SpaHeaterSwitch(coordinator, entry)
    heater.hass = FakeHass()
    heater.async_write_ha_state = MagicMock()

    import custom_components.joyonway.switch as switch_mod

    original = switch_mod.OPTIMISTIC_TIMEOUT_SECONDS
    switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = 5.0

    try:
        await heater.async_turn_on()
        pending_task = heater._pending_task
        assert pending_task is not None

        await heater.async_will_remove_from_hass()
        assert heater._pending_task is None
        # Task cancel was requested; let it propagate
        await asyncio.sleep(0)
        assert pending_task.done()
    finally:
        switch_mod.OPTIMISTIC_TIMEOUT_SECONDS = original


@pytest.mark.asyncio
async def test_sync_frame_detection(coordinator):
    """Detecting a sync frame sets the sync_frame_event."""
    coordinator._sync_frame_event.clear()

    sync_frame = b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d"
    result, consumed = coordinator._try_parse_buffer(bytearray(sync_frame))

    assert result is None
    assert consumed == len(sync_frame)
    assert coordinator._sync_frame_event.is_set()


@pytest.mark.asyncio
async def test_send_command_with_sync_success(coordinator):
    """async_send_command succeeds when sync frame event is set."""
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    coordinator._writer = mock_writer
    coordinator._sync_timeout = 0.1
    coordinator._sync_frame_event.clear()

    # Trigger the sync frame event after a brief delay
    async def trigger_event():
        await asyncio.sleep(0.01)
        coordinator._sync_frame_event.set()

    task = asyncio.create_task(trigger_event())

    result = await coordinator.async_send_command(b"\xab\xcd")
    await task

    assert result is True
    mock_writer.write.assert_called_once_with(b"\xab\xcd")
    mock_writer.drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_command_with_sync_timeout(coordinator):
    """async_send_command fails when sync frame event times out."""
    mock_writer = MagicMock()
    mock_writer.write = MagicMock()
    mock_writer.drain = AsyncMock()
    coordinator._writer = mock_writer
    coordinator._sync_timeout = 0.01
    coordinator._sync_frame_event.clear()

    result = await coordinator.async_send_command(b"\xab\xcd")

    assert result is False
    mock_writer.write.assert_not_called()
    mock_writer.drain.assert_not_called()
