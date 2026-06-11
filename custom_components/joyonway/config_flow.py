"""Config flow for Joyonway spa controllers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import (
    CONF_MODEL,
    DEFAULT_HOST,
    DEFAULT_MODEL,
    DEFAULT_PORT,
    DOMAIN,
)
from .protocol import find_frames_with_indices

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def _detect_model(host: str, port: int, timeout: float = 5.0) -> str | None:
    """Test TCP connection and auto-detect the controller model from a broadcast frame."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )

        # Read stream until we find a full broadcast frame
        buf = bytearray()
        detected_model = DEFAULT_MODEL
        end_time = asyncio.get_running_loop().time() + timeout

        while asyncio.get_running_loop().time() < end_time:
            time_left = end_time - asyncio.get_running_loop().time()
            if time_left <= 0:
                break

            chunk = await asyncio.wait_for(reader.read(1024), timeout=time_left)
            if not chunk:
                break

            buf.extend(chunk)
            frames = find_frames_with_indices(bytes(buf))
            for raw_frame, _ in frames:
                # Need at least 9 bytes to read index 8
                if len(raw_frame) > 8 and raw_frame[1] == 0xFF:
                    if raw_frame[8] == 0x02:
                        detected_model = "P23B32"
                    elif raw_frame[8] == 0x03:
                        detected_model = "P25B85"

                    writer.close()
                    await writer.wait_closed()
                    return detected_model

        writer.close()
        await writer.wait_closed()
        return None
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Connection test and detect %s:%s failed: %s", host, port, err)
        return None


class JoyonwayConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Joyonway spa controllers."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step — bridge IP and port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]

            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()

            detected_model = await _detect_model(host, port)
            if detected_model is not None:
                return self.async_create_entry(
                    title=f"Joyonway Spa ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_MODEL: detected_model,
                    },
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
