"""Data update coordinator for Joyonway P25B85 spa integration.

Connects to the RS485 bridge via TCP, reads broadcast frames, and
parses them through the model adapter.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .adapters import get_adapter, ModelAdapter
from .const import DOMAIN, SCAN_INTERVAL, TCP_TIMEOUT
from .protocol import find_frames, unescape_frame, is_broadcast, validate_frame

_LOGGER = logging.getLogger(__name__)


class JoyonwayP25B85Coordinator(DataUpdateCoordinator):
    """Coordinator that polls the RS485 bridge for broadcast frames."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, model: str) -> None:
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
        self._adapter: ModelAdapter = get_adapter(model)
        self._available = False

    @property
    def available(self) -> bool:
        """Return True if the bridge is reachable and data is valid."""
        return self._available

    @property
    def adapter(self) -> ModelAdapter:
        """Return the model adapter."""
        return self._adapter

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
        return data

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
            data = self._adapter.parse_status(logical)
            if data is not None:
                return data

        return None

