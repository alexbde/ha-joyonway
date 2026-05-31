"""Config flow for Joyonway P25B85."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT

from .const import CONF_MODEL, DEFAULT_HOST, DEFAULT_MODEL, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def _test_connection(host: str, port: int, timeout: float = 5.0) -> bool:
    """Test TCP connection to the RS485 bridge."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Connection test %s:%s failed: %s", host, port, err)
        return False


class JoyonwayP25B85ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Joyonway P25B85."""

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

            if await _test_connection(host, port):
                return self.async_create_entry(
                    title=f"Joyonway P25B85 ({host})",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_MODEL: DEFAULT_MODEL,
                    },
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

