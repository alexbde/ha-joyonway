"""Data update coordinator for Joyonway P25B85 spa integration.

Maintains a persistent TCP connection to the RS485 bridge, continuously
parsing broadcast frames. Commands are sent on the same socket.
Reconnects automatically with exponential backoff on any error.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .adapters import get_adapter, ModelAdapter
from .const import (
    AVAILABILITY_GRACE_SECONDS,
    CLOCK_SYNC_COOLDOWN,
    CLOCK_SYNC_DRIFT_THRESHOLD,
    COMMAND_COOLDOWN,
    DOMAIN,
    OPT_AUTO_SYNC_CLOCK,
    OPT_OZONE_MODE,
    OZONE_MODE_AUTO,
    RX_STALE_SECONDS,
    SCAN_INTERVAL,
    TCP_TIMEOUT,
)
from .protocol import find_frames, unescape_frame, is_broadcast, validate_frame

_LOGGER = logging.getLogger(__name__)


class JoyonwayP25B85Coordinator(DataUpdateCoordinator):
    """Coordinator with persistent TCP connection and background reader."""

    def __init__(
        self, hass: HomeAssistant, host: str, port: int, model: str, entry: ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            # Fallback poll interval — health check only
            update_interval=timedelta(seconds=SCAN_INTERVAL),
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

        # Command pacing
        self._last_command_ts: float = 0.0

        # Clock sync
        self._last_clock_sync_ts: float = 0.0
        self.last_detected_ozone_mode: str | None = None

    @property
    def available(self) -> bool:
        """True while connected, or briefly after disconnect to avoid flicker."""
        if self._available:
            return True
        if self.data is None or self._disconnect_ts is None:
            return False
        return (time.monotonic() - self._disconnect_ts) <= AVAILABILITY_GRACE_SECONDS

    @property
    def adapter(self) -> ModelAdapter:
        """Return the model adapter."""
        return self._adapter

    @property
    def ozone_mode(self) -> str:
        """Return the configured ozone mode (auto or manual)."""
        return self.entry.options.get(OPT_OZONE_MODE, OZONE_MODE_AUTO)

    @property
    def auto_sync_clock(self) -> bool:
        """Return whether automatic clock sync is enabled."""
        return self.entry.options.get(OPT_AUTO_SYNC_CLOCK, False)

    # ── Setup / lifecycle ────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Establish the persistent connection and start the reader."""
        await self._connect()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._first_data_event.wait(), timeout=TCP_TIMEOUT)

    async def async_shutdown(self) -> None:
        """Close connection on shutdown."""
        self._stopped = True
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
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
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

                    # Ozone mode sync + clock sync (moved from _async_update_data)
                    self._sync_ozone_mode(result)
                    await self._check_clock_drift(result)

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

    def _try_parse_buffer(self, buf: bytes) -> tuple[dict | None, int]:
        """Return (parsed_data, consumed_bytes)."""
        frames = find_frames(bytes(buf))
        if not frames:
            return None, 0

        # Calculate consumed bytes up to the end of the last frame found
        last_frame = frames[-1]
        last_end = buf.rfind(last_frame) + len(last_frame)

        for raw_frame in frames:
            if not validate_frame(raw_frame):
                continue
            if not is_broadcast(raw_frame):
                continue

            logical = unescape_frame(raw_frame, full=self._adapter.unescape_full_frame)

            try:
                data = self._adapter.parse_status(logical)
            except Exception:
                _LOGGER.exception("Adapter parse failed for frame: %s", logical.hex())
                continue
            if data is not None:
                return data, last_end

        return None, last_end

    # ── Command sending ──────────────────────────────────────────────

    async def async_send_command(self, frame: bytes) -> bool:
        """Send a command frame on the persistent connection."""
        if self._writer is None:
            _LOGGER.error("Cannot send command: not connected")
            self._schedule_reconnect()
            return False

        async with self._write_lock:
            elapsed = time.monotonic() - self._last_command_ts
            if elapsed < COMMAND_COOLDOWN:
                await asyncio.sleep(COMMAND_COOLDOWN - elapsed)

            try:
                self._writer.write(frame)
                await self._writer.drain()
                self._last_command_ts = time.monotonic()
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

    # ── Ozone mode sync ──────────────────────────────────────────────

    def _sync_ozone_mode(self, data: dict) -> None:
        """Sync ozone mode config option with spa's broadcast value."""
        spa_ozone_mode = data.get("ozone_mode")
        if spa_ozone_mode is not None:
            self.last_detected_ozone_mode = spa_ozone_mode
            if spa_ozone_mode != self.ozone_mode:
                _LOGGER.info(
                    "Ozone mode: spa reports '%s', updating config (was '%s')",
                    spa_ozone_mode, self.ozone_mode,
                )
                new_options = {**self.entry.options, OPT_OZONE_MODE: spa_ozone_mode}
                self.hass.config_entries.async_update_entry(
                    self.entry, options=new_options
                )

    # ── Clock drift check ────────────────────────────────────────────

    async def _check_clock_drift(self, data: dict) -> None:
        """Sync spa clock if drift exceeds threshold (with cooldown)."""
        if not self.auto_sync_clock:
            return

        spa_dt = data.get("spa_datetime")
        if spa_dt is None:
            return

        now_ts = time.monotonic()
        if now_ts - self._last_clock_sync_ts < CLOCK_SYNC_COOLDOWN:
            return

        now = dt_util.now()
        try:
            if isinstance(spa_dt, datetime):
                drift = abs((now - spa_dt).total_seconds())
            else:
                return
        except (TypeError, ValueError):
            return

        if drift > CLOCK_SYNC_DRIFT_THRESHOLD:
            _LOGGER.info("Spa clock drift is %.0fs, syncing to HA time", drift)
            frame = self._adapter.build_datetime_command(
                year=now.year,
                month=now.month,
                day=now.day,
                hour=now.hour,
                minute=now.minute,
                second=now.second,
            )
            success = await self.async_send_command(frame)
            if success:
                self._last_clock_sync_ts = now_ts
                _LOGGER.debug("Auto clock sync completed")
            else:
                _LOGGER.warning("Auto clock sync failed")

