"""Data update coordinator for Joyonway spa integration.

Maintains a persistent TCP connection to the RS485 bridge, continuously
parsing broadcast frames. Commands are sent on the same socket.
Reconnects automatically with exponential backoff on any error.

Includes an IntentQueue that coalesces rapid user actions, prevents bus
contention, and automatically cancels reverted intents.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .adapters import get_adapter, ModelAdapter
from .const import (
    AVAILABILITY_GRACE_SECONDS,
    CLOCK_SYNC_COOLDOWN,
    CLOCK_SYNC_DRIFT_THRESHOLD,
    DOMAIN,
    INTENT_COALESCE_SECONDS,
    OPT_AUTO_SYNC_CLOCK,
    OZONE_MODE_AUTO,
    RX_STALE_SECONDS,
    SCAN_INTERVAL,
    TCP_TIMEOUT,
)
from .protocol import (
    SYNC_FRAME,
    find_frames_with_indices,
    unescape_frame,
    is_broadcast,
    validate_frame,
)

_LOGGER = logging.getLogger(__name__)


class IntentBuildError(Exception):
    """Raised when an intent cannot be built due to invalid/missing prerequisites."""


def _default_verify(overrides: dict[str, Any], data: dict[str, Any] | None) -> bool:
    """Default verification function: checks if all overrides keys match in coordinator data."""
    if data is None:
        return False
    for k, v in overrides.items():
        if data.get(k) != v:
            return False
    return True


@dataclass
class _PendingGroup:
    """A batch of coalesced intents for one group."""

    overrides: dict[str, Any]
    build_fn: Callable[[dict[str, Any], dict[str, Any] | None], bytes | None]
    on_failure_callbacks: list[Callable[[], None]] = field(default_factory=list)
    verify_fn: Callable[[dict[str, Any], dict[str, Any] | None], bool] | None = None


class IntentQueue:
    """Queue that coalesces rapid user intents and drains them sequentially.

    Key behaviors:
    - Same-group intents merge within a coalesce window (300ms from first intent)
    - If merged overrides equal current state at drain time → no-op, skip entirely
    - Sequential drain ensures only one command is on the bus at a time
    - Retry once on TCP send failure
    - Accidental toggles (ON→OFF) cancel out automatically via no-op detection
    """

    def __init__(
        self,
        coordinator: "JoyonwayCoordinator",
        coalesce_seconds: float = INTENT_COALESCE_SECONDS,
    ) -> None:
        self._coordinator = coordinator
        self._coalesce_seconds = coalesce_seconds
        self._pending: dict[str, _PendingGroup] = {}
        self._flush_task: asyncio.Task | None = None
        self._drain_lock = asyncio.Lock()

    def submit(
        self,
        group: str,
        overrides: dict[str, Any],
        build_fn: Callable[[dict[str, Any], dict[str, Any] | None], bytes | None],
        on_failure: Callable[[], None] | None = None,
        verify_fn: Callable[[dict[str, Any], dict[str, Any] | None], bool]
        | None = None,
    ) -> None:
        """Submit an intent for coalescing and eventual execution.

        Args:
            group: Coalescing key. Same-group intents merge their overrides.
            overrides: Key-value pairs representing desired state changes.
            build_fn(merged_overrides, coordinator_data) -> frame or None:
                Called at drain time. Returns wire-ready bytes, or None to
                signal a no-op (skip sending).
            on_failure: Called if the command ultimately fails (after retries).
            verify_fn: Optional function to verify state convergence.
        """
        if group in self._pending:
            pg = self._pending[group]
            pg.overrides.update(overrides)
            pg.build_fn = build_fn
            pg.verify_fn = verify_fn
            if on_failure:
                pg.on_failure_callbacks.append(on_failure)
        else:
            self._pending[group] = _PendingGroup(
                overrides=dict(overrides),
                build_fn=build_fn,
                on_failure_callbacks=[on_failure] if on_failure else [],
                verify_fn=verify_fn,
            )

        # Start the coalesce timer if not already running
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = self._coordinator.hass.async_create_task(
                self._flush_after_window()
            )

    async def _flush_after_window(self) -> None:
        """Wait for the coalesce window then drain all pending groups."""
        await asyncio.sleep(self._coalesce_seconds)
        await self._drain_all()

    async def _drain_all(self) -> None:
        """Drain all pending groups sequentially under lock."""
        async with self._drain_lock:
            # Snapshot and clear pending — new intents during drain go into next batch
            groups = self._pending
            self._pending = {}
            self._flush_task = None

            for group_key, group_data in groups.items():
                await self._process_group(group_key, group_data)

        # If new intents accumulated during drain, flush them immediately
        if self._pending and (self._flush_task is None or self._flush_task.done()):
            self._flush_task = self._coordinator.hass.async_create_task(
                self._flush_after_window()
            )

    async def _process_group(self, group_key: str, group: _PendingGroup) -> None:
        """Build and send the coalesced command for one group with verification retries."""
        from homeassistant.core import callback

        data = self._coordinator.data
        try:
            frame = group.build_fn(group.overrides, data)
        except IntentBuildError as err:
            _LOGGER.error("Intent queue [%s]: %s", group_key, err)
            self._run_failure_callbacks(group_key, group)
            return
        except Exception:
            _LOGGER.exception("Intent queue [%s]: unexpected build error", group_key)
            self._run_failure_callbacks(group_key, group)
            return

        if frame is None:
            # No-op: merged intent matches current state (e.g., toggled ON then OFF)
            _LOGGER.debug("Intent queue [%s]: no-op detected, skipping", group_key)
            return

        # Use custom verify function or default key-matching check
        verify_fn = group.verify_fn or _default_verify

        # Set up temporary coordinator listener to track incoming broadcasts
        update_event = asyncio.Event()
        broadcast_count = 0

        @callback
        def on_update() -> None:
            nonlocal broadcast_count
            broadcast_count += 1
            update_event.set()

        self._coordinator.register_data_callback(on_update)

        converged = False
        max_attempts = 3  # 1 initial + 2 retries (total 3)

        try:
            for attempt in range(max_attempts):
                # Reset broadcast count for this write attempt
                broadcast_count = 0

                success = await self._coordinator.async_send_command(frame)
                if not success:
                    _LOGGER.warning(
                        "Intent queue [%s]: send failed on attempt %d",
                        group_key,
                        attempt + 1,
                    )
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(1.0)
                    continue

                # Wait for convergence: up to 2 broadcasts or 4.0 seconds safety timeout
                start_time = time.monotonic()
                while time.monotonic() - start_time < 4.0:
                    if verify_fn(group.overrides, self._coordinator.data):
                        converged = True
                        break

                    if broadcast_count >= 2:
                        break

                    remaining = 4.0 - (time.monotonic() - start_time)
                    if remaining <= 0:
                        break

                    update_event.clear()
                    try:
                        await asyncio.wait_for(update_event.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        _LOGGER.warning(
                            "Intent queue [%s]: timed out waiting for broadcast updates on attempt %d",
                            group_key,
                            attempt + 1,
                        )
                        break

                if converged:
                    _LOGGER.debug(
                        "Intent queue [%s]: converged to target state on attempt %d",
                        group_key,
                        attempt + 1,
                    )
                    break

                if attempt < max_attempts - 1:
                    _LOGGER.warning(
                        "Intent queue [%s]: not converged after %d broadcasts on attempt %d, retrying in 0.5s...",
                        group_key,
                        broadcast_count,
                        attempt + 1,
                    )
                    await asyncio.sleep(0.5)
        finally:
            self._coordinator.unregister_data_callback(on_update)

        if not converged:
            _LOGGER.error(
                "Intent queue [%s]: failed to converge to target state after %d attempts",
                group_key,
                max_attempts,
            )
            self._run_failure_callbacks(group_key, group)

    @staticmethod
    def _run_failure_callbacks(group_key: str, group: _PendingGroup) -> None:
        """Run registered failure callbacks safely."""
        for cb in group.on_failure_callbacks:
            try:
                cb()
            except Exception:
                _LOGGER.exception(
                    "Intent queue [%s]: on_failure callback error", group_key
                )

    async def shutdown(self) -> None:
        """Cancel pending flush task on shutdown."""
        if self._flush_task is not None:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

    async def flush(self) -> None:
        """Immediately drain pending intents (used before config-entry reload)."""
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None
        if self._pending:
            await self._drain_all()


class JoyonwayCoordinator(DataUpdateCoordinator):
    """Coordinator with persistent TCP connection and background reader."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        model: str,
        entry: JoyonwayConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Fallback poll interval — health check only
            update_interval=timedelta(seconds=SCAN_INTERVAL),
            config_entry=entry,
        )
        self.host = host
        self.port = port
        self.model = model
        self.entry = entry
        self._adapter: ModelAdapter = get_adapter(model)
        self._available = False

        # Persistent connection state
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay = 1.0
        self._stopped = False
        self._last_rx_ts: float = 0.0
        self._disconnect_ts: float | None = None
        self._first_data_event = asyncio.Event()

        # Command pacing & synchronization
        self._sync_frame_event = asyncio.Event()
        self._sync_timeout = 2.0

        # Intent queue for coalescing and serializing user actions
        self.intent_queue = IntentQueue(self)
        self._on_data_callbacks: list[Callable[[], None]] = []

        # Clock sync
        self._last_clock_sync_ts = 0.0
        self._last_clock_sync_attempt_ts = 0.0
        self.last_detected_ozone_mode = None
        self._last_ozone_sync_check_ts = 0.0
        self._clock_check_unsub: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        """True while connected, or briefly after disconnect to avoid flicker."""
        if self._available:
            return True
        if self.data is None or self._disconnect_ts is None:
            return False
        return (time.monotonic() - self._disconnect_ts) <= AVAILABILITY_GRACE_SECONDS

    @property
    def is_connected(self) -> bool:
        """Return strict bridge connection state (without grace window)."""
        return self._available

    @property
    def adapter(self) -> ModelAdapter:
        """Return the model adapter."""
        return self._adapter

    @property
    def has_blower(self) -> bool:
        """Return whether the spa has a blower, from broadcast or adapter default."""
        if self.data is not None and "blower_present" in self.data:
            return self.data["blower_present"]
        return self._adapter.has_blower

    @property
    def ozone_mode(self) -> str:
        """Return the current ozone mode (auto or manual)."""
        if self.data is None:
            return OZONE_MODE_AUTO
        return self.data.get("ozone_mode", OZONE_MODE_AUTO)

    @property
    def heater_mode(self) -> str:
        """Return the current heater mode (auto or manual)."""
        if self.data is None:
            return "auto"
        return self.data.get("heater_mode", "auto")

    @property
    def auto_sync_clock(self) -> bool:
        """Return whether automatic clock sync is enabled."""
        return self.entry.options.get(OPT_AUTO_SYNC_CLOCK, False)

    def async_set_updated_data(self, data: Any) -> None:
        """Set new data and notify listeners."""
        super().async_set_updated_data(data)
        for cb in list(self._on_data_callbacks):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Error in coordinator data update callback")

    # ── Setup / lifecycle ────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Establish the persistent connection and start the reader."""
        await self._connect()
        from homeassistant.helpers.event import async_track_time_interval

        self._clock_check_unsub = async_track_time_interval(
            self.hass, self._periodic_clock_check, timedelta(seconds=60)
        )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._first_data_event.wait(), timeout=TCP_TIMEOUT)

    async def async_shutdown(self) -> None:
        """Close connection on shutdown."""
        self._stopped = True
        if self._clock_check_unsub is not None:
            self._clock_check_unsub()
            self._clock_check_unsub = None
        await self.intent_queue.shutdown()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._close_connection()

    # ── Connection management ────────────────────────────────────────

    async def _connect(self) -> None:
        """Open TCP connection and start the background reader task."""
        if self._stopped:
            return
        async with self._connect_lock:
            if self._stopped or self._writer is not None:
                return
            try:
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=TCP_TIMEOUT,
                )
                self._available = True
                self._disconnect_ts = None
                self._reconnect_delay = 1.0
                self._reader_task = self.hass.async_create_task(self._reader_loop())
                _LOGGER.info("RS485 bridge connected: %s:%s", self.host, self.port)
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.warning("RS485 bridge connection failed: %s", err)
                self._available = False
                self._schedule_reconnect()

    async def _close_connection(self) -> None:
        """Close the TCP connection and cancel the reader task."""
        if self._writer is not None:
            writer = self._writer
            writer.close()
            self._writer = None
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        self._reader = None
        if (
            self._reader_task is not None
            and self._reader_task is not asyncio.current_task()
        ):
            self._reader_task.cancel()
            self._reader_task = None

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if self._stopped:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        delay = self._reconnect_delay
        self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
        _LOGGER.info("Reconnecting in %.0fs", delay)
        self._reconnect_task = self.hass.async_create_task(self._reconnect_after(delay))

    async def _reconnect_after(self, delay: float) -> None:
        """Wait then reconnect."""
        await asyncio.sleep(delay)
        if not self._stopped:
            await self._connect()

    # ── Reader loop ──────────────────────────────────────────────────

    async def _reader_loop(self) -> None:
        """Read TCP stream and parse broadcast frames continuously."""
        reader = self._reader
        if reader is None:
            return
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    _LOGGER.warning("RS485 bridge disconnected (EOF)")
                    break
                buf.extend(chunk)

                result, consumed = self._try_parse_buffer(buf)
                if result is not None:
                    self._last_rx_ts = time.monotonic()
                    self._disconnect_ts = None
                    self._first_data_event.set()

                    self.async_set_updated_data(result)
                if consumed:
                    del buf[:consumed]

                # Prevent unbounded growth
                if len(buf) > 8192:
                    buf = buf[-2048:]
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            self._disconnect_ts = time.monotonic()
            self._available = False
            await self._close_connection()
            if not self._stopped:
                self._schedule_reconnect()

    # ── Buffer parsing ───────────────────────────────────────────────

    def _handle_sync_frame(self) -> None:
        """Handle a synchronization frame received on the bus."""
        _LOGGER.debug("RS485 sync frame received")
        self._sync_frame_event.set()

    def _try_parse_buffer(self, buf: bytes | bytearray) -> tuple[dict | None, int]:
        """Return (parsed_data, consumed_bytes)."""
        frames = find_frames_with_indices(bytes(buf))
        if not frames:
            return None, 0

        # Calculate consumed bytes up to the end of the last frame found
        _, last_end = frames[-1]

        latest_data: dict | None = None
        for raw_frame, _ in frames:
            if raw_frame == SYNC_FRAME:
                self._handle_sync_frame()
                continue

            if not is_broadcast(raw_frame):
                continue
            if not validate_frame(
                raw_frame, unescape_full=self._adapter.unescape_full_frame
            ):
                continue

            logical = unescape_frame(
                raw_frame, unescape_full=self._adapter.unescape_full_frame
            )

            try:
                data = self._adapter.parse_status(logical)
            except (IndexError, ValueError, KeyError):
                _LOGGER.exception("Adapter parse failed for frame: %s", logical.hex())
                continue
            if data is not None:
                latest_data = data

        return latest_data, last_end

    # ── Command sending ──────────────────────────────────────────────

    async def async_send_command(self, frame: bytes) -> bool:
        """Send a command frame on the persistent connection aligned with the quiet bus window."""
        if self._writer is None:
            _LOGGER.error("Cannot send command: not connected")
            self._schedule_reconnect()
            return False

        async with self._write_lock:
            if self._sync_timeout > 0.0:
                self._sync_frame_event.clear()
                try:
                    # Wait for a synchronization frame to align with the quiet window
                    await asyncio.wait_for(
                        self._sync_frame_event.wait(), timeout=self._sync_timeout
                    )
                    # Small delay required by the protocol to let the controller finish processing
                    await asyncio.sleep(0.03)
                    _LOGGER.debug(
                        "Sync frame received, sending command aligned with quiet window"
                    )
                except asyncio.TimeoutError:
                    _LOGGER.error(
                        "Timeout waiting for sync frame (%.1fs), aborting command send",
                        self._sync_timeout,
                    )
                    return False

            try:
                self._writer.write(frame)
                await self._writer.drain()
                _LOGGER.debug("Sent command: %s", frame.hex())
                return True
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Command send failed: %s", err)
                await self._close_connection()
                self._schedule_reconnect()
                return False

    # ── Fallback poll (health check) ─────────────────────────────────

    async def _async_update_data(self) -> dict:
        """Fallback: if persistent connection is alive, return cached data."""
        now_ts = time.monotonic()
        if self._available and self.data is not None:
            if now_ts - self._last_rx_ts > RX_STALE_SECONDS:
                _LOGGER.warning(
                    "No RX data for %.0fs, reconnecting",
                    now_ts - self._last_rx_ts,
                )
                await self._close_connection()
                self._available = False
                self._disconnect_ts = now_ts
                self._schedule_reconnect()
            else:
                return self.data
        if not self._available:
            await self._connect()
        if self.data is None:
            raise UpdateFailed("No data from RS485 bridge")
        return self.data

    # ── Clock drift check ────────────────────────────────────────────

    def _check_clock_drift(self, data: dict) -> None:
        """Sync spa clock if drift exceeds threshold (with cooldown)."""
        if not self.auto_sync_clock:
            return

        spa_dt = data.get("spa_datetime")
        if spa_dt is None:
            return

        now_ts = time.monotonic()
        last_sync_event_ts = max(
            self._last_clock_sync_ts, self._last_clock_sync_attempt_ts
        )
        if now_ts - last_sync_event_ts < CLOCK_SYNC_COOLDOWN:
            return

        now = dt_util.now()
        try:
            if isinstance(spa_dt, datetime):
                # spa_datetime is tagged with HA's local timezone (dt_util.DEFAULT_TIME_ZONE)
                # by the adapter, so both datetimes are in the same tz — comparison is valid.
                # The resulting now.hour / minute / second are local time, which is what the
                # spa controller expects (it stores local time with no timezone awareness).
                drift = abs((now - spa_dt).total_seconds())
            else:
                return
        except (TypeError, ValueError):
            return

        if drift > CLOCK_SYNC_DRIFT_THRESHOLD:
            self._last_clock_sync_attempt_ts = now_ts
            self._last_clock_sync_ts = now_ts  # optimistic; failure logged via callback
            _LOGGER.info("Spa clock drift is %.0fs, syncing to HA time", drift)

            def _build_time_sync(
                overrides: dict[str, Any], _data: dict | None
            ) -> bytes:
                return self._adapter.build_time_command(
                    year=overrides["year"],
                    month=overrides["month"],
                    day=overrides["day"],
                    hour=overrides["hour"],
                    minute=overrides["minute"],
                    second=overrides["second"],
                )

            def _build_date_sync(
                overrides: dict[str, Any], _data: dict | None
            ) -> bytes:
                return self._adapter.build_date_command(
                    year=overrides["year"],
                    month=overrides["month"],
                    day=overrides["day"],
                    hour=overrides["hour"],
                    minute=overrides["minute"],
                    second=overrides["second"],
                )

            def _on_clock_failure() -> None:
                _LOGGER.warning("Auto clock sync failed")

            # 1. Update Time first (prefix 0x50) so current spa time matches HA time
            self.intent_queue.submit(
                group="clock_sync_time",
                overrides={
                    "year": now.year,
                    "month": now.month,
                    "day": now.day,
                    "hour": now.hour,
                    "minute": now.minute,
                    "second": now.second,
                },
                build_fn=_build_time_sync,
                on_failure=_on_clock_failure,
                verify_fn=lambda overrides, data: True,
            )

            # 2. Update Date second (prefix 0x05) with matching time fields to satisfy hardware validation
            self.intent_queue.submit(
                group="clock_sync_date",
                overrides={
                    "year": now.year,
                    "month": now.month,
                    "day": now.day,
                    "hour": now.hour,
                    "minute": now.minute,
                    "second": now.second,
                },
                build_fn=_build_date_sync,
                on_failure=_on_clock_failure,
                verify_fn=lambda overrides, data: True,
            )

    @callback
    def _periodic_clock_check(self, _now: datetime) -> None:
        """Periodic callback to check clock drift."""
        if self.data is not None:
            self._check_clock_drift(self.data)

    @callback
    def register_data_callback(self, callback_fn: Callable[[], None]) -> None:
        """Register a callback for data updates."""
        self._on_data_callbacks.append(callback_fn)

    @callback
    def unregister_data_callback(self, callback_fn: Callable[[], None]) -> None:
        """Unregister a callback for data updates."""
        if callback_fn in self._on_data_callbacks:
            self._on_data_callbacks.remove(callback_fn)


type JoyonwayConfigEntry = ConfigEntry[JoyonwayCoordinator]
