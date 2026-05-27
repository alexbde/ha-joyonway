"""Data update coordinator for Joyonway P25B85 spa integration.

Connects to the RS485 bridge via TCP, reads broadcast frames, and
parses them through the model adapter. Also sends command frames for
write support (light, pump).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .adapters import get_adapter, ModelAdapter
from .const import (
    CLOCK_SYNC_COOLDOWN,
    CLOCK_SYNC_DRIFT_THRESHOLD,
    DOMAIN,
    OPT_AUTO_SYNC_CLOCK,
    OPT_OZONE_MODE,
    OZONE_MODE_AUTO,
    SCAN_INTERVAL,
    TCP_TIMEOUT,
)
from .protocol import find_frames, unescape_frame, is_broadcast, validate_frame

_LOGGER = logging.getLogger(__name__)

# Minimum time between commands to avoid flooding the bus
COMMAND_COOLDOWN = 1.0

class JoyonwayP25B85Coordinator(DataUpdateCoordinator):
    """Coordinator that polls the RS485 bridge for broadcast frames."""

    def __init__(
        self, hass: HomeAssistant, host: str, port: int, model: str, entry: ConfigEntry
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL),
        )
        self.host = host
        self.port = port
        self.model = model
        self.entry = entry
        self._adapter: ModelAdapter = get_adapter(model)
        self._available = False
        self._command_lock = asyncio.Lock()
        self._last_command_ts = 0.0
        self._last_clock_sync_ts: float = 0.0

    @property
    def available(self) -> bool:
        """Return True if the bridge is reachable and data is valid."""
        return self._available

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
        return self.entry.options.get(OPT_AUTO_SYNC_CLOCK, True)

    async def async_apply_ozone_mode(self) -> None:
        """Send the ozone mode command to match the configured option.

        Called once after setup to ensure the controller state matches.
        """
        mode = self.ozone_mode
        try:
            cmd = self._adapter.build_ozone_mode_command(mode)
            success = await self.async_send_command(cmd)
            if success:
                _LOGGER.debug("Ozone mode set to %s", mode)
            else:
                _LOGGER.warning("Failed to send ozone mode command (%s)", mode)
        except Exception:
            _LOGGER.exception("Error sending ozone mode command")

    async def _async_update_data(self) -> dict:
        """Fetch data from the RS485 bridge.

        Connects, reads until a valid broadcast frame is found, parses it.
        """
        data = await self._read_broadcast()
        if data is None:
            self._available = False
            raise UpdateFailed(
                f"No broadcast frame from RS485 bridge {self.host}:{self.port}"
            )
        self._available = True

        # Auto clock sync if enabled
        if self.auto_sync_clock:
            await self._check_clock_drift(data)

        return data

    async def _check_clock_drift(self, data: dict) -> None:
        """Sync spa clock if drift exceeds threshold (with cooldown)."""
        spa_dt = data.get("spa_datetime")
        if spa_dt is None:
            return

        loop = asyncio.get_running_loop()
        now_ts = loop.time()
        if now_ts - self._last_clock_sync_ts < CLOCK_SYNC_COOLDOWN:
            return

        now = dt_util.now()
        try:
            if isinstance(spa_dt, datetime):
                drift = abs((now - spa_dt).total_seconds())
            else:
                # spa_dt might be a string
                return
        except (TypeError, ValueError):
            return

        if drift > CLOCK_SYNC_DRIFT_THRESHOLD:
            _LOGGER.info(
                "Spa clock drift is %.0fs, syncing to HA time", drift
            )
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

    async def _read_broadcast(self) -> dict | None:
        """Connect to bridge and read one broadcast frame."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=TCP_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.debug("RS485 bridge connection failed: %s", err)
            return None

        loop = asyncio.get_running_loop()
        deadline = loop.time() + TCP_TIMEOUT
        try:
            buf = bytearray()
            while loop.time() < deadline:
                try:
                    chunk = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                    if not chunk:
                        break
                    buf.extend(chunk)

                    # Try to find a valid broadcast frame in accumulated data
                    result = self._try_parse_buffer(buf)
                    if result is not None:
                        return result

                    # Prevent unbounded buffer growth
                    if len(buf) > 8192:
                        buf = buf[-2048:]
                except asyncio.TimeoutError:
                    continue

            _LOGGER.debug(
                "RS485 bridge timeout: %d bytes read, no valid broadcast", len(buf)
            )
            return None
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _try_parse_buffer(self, buf: bytes) -> dict | None:
        """Try to extract and parse a broadcast frame from the buffer."""
        frames = find_frames(bytes(buf))
        for raw_frame in frames:
            if not validate_frame(raw_frame):
                continue
            if not is_broadcast(raw_frame):
                continue

            # Apply model-specific unescape policy
            logical = unescape_frame(raw_frame, full=self._adapter.unescape_full_frame)

            # Parse through adapter
            try:
                data = self._adapter.parse_status(logical)
            except Exception:
                _LOGGER.exception("Adapter parse failed for frame: %s", logical.hex())
                continue
            if data is not None:
                return data

        return None

    async def async_send_command(self, frame: bytes) -> bool:
        """Send a raw command frame to the RS485 bridge.

        Opens a TCP connection, writes the frame, then closes.
        Returns True on success, False on failure.
        """
        async with self._command_lock:
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - self._last_command_ts
            if elapsed < COMMAND_COOLDOWN:
                await asyncio.sleep(COMMAND_COOLDOWN - elapsed)

            try:
                _reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port),
                    timeout=TCP_TIMEOUT,
                )
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Failed to connect for command send: %s", err)
                return False

            try:
                writer.write(frame)
                await writer.drain()
                # Brief pause to let the controller process
                await asyncio.sleep(0.1)
                _LOGGER.debug("Sent command frame: %s", frame.hex())
                self._last_command_ts = loop.time()
                return True
            except (OSError, asyncio.TimeoutError) as err:
                _LOGGER.error("Failed to send command: %s", err)
                return False
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
