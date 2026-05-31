# 2026-05-13 | Config flow | IP/port entry and TCP connection test | Depends: rs485.py, const.py
"""Config flow for Joyonway P23B32."""
from __future__ import annotations
import logging
from typing import Any
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from .const import DEFAULT_HOST, DEFAULT_PORT, DOMAIN
from .rs485 import test_connection

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)

class JoyonwayP23B32ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Joyonway P23B32."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            await self.async_set_unique_id(f"{host}:{port}")
            self._abort_if_unique_id_configured()
            if await test_connection(host, port):
                return self.async_create_entry(
                    title=f"Joyonway P23B32 ({host})",
                    data={CONF_HOST: host, CONF_PORT: port},
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
