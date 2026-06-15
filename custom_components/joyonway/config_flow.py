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
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .adapters import ADAPTERS
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

SIGNATURE_MODEL_MAP: dict[int, str] = {
    0x01: "P20B29",
    0x02: "P23B32",
    0x03: "P25B85",
}


def _model_schema(default: str | None = None) -> vol.Schema:
    """Build the model selector schema dynamically from available adapters."""
    options = sorted(ADAPTERS.keys())
    selector = SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key="model",
        )
    )
    field = (
        vol.Required(CONF_MODEL, default=default)
        if default
        else vol.Required(CONF_MODEL)
    )
    return vol.Schema({field: selector})


async def _detect_model(host: str, port: int, timeout: float = 5.0) -> str | None:
    """Test TCP connection and auto-detect the controller model from a broadcast frame.

    Returns:
    - str (model name hint) if connected and signature recognized
    - "" (empty string) if connected but signature unknown
    - None if connection failed or times out
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )

        # Read stream until we find a full broadcast frame
        buf = bytearray()
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
                    sig = raw_frame[8]
                    detected_model = SIGNATURE_MODEL_MAP.get(sig, "")
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

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host = ""
        self._port = DEFAULT_PORT
        self._detected_model: str | None = None

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

            detected_model_raw = await _detect_model(host, port)
            if detected_model_raw is not None:
                self._host = host
                self._port = port
                if detected_model_raw in ADAPTERS:
                    self._detected_model = detected_model_raw
                    return await self.async_step_model_confirm()
                self._detected_model = None
                return await self.async_step_model_confirm_manual()
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_model_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the auto-detected spa model."""
        if user_input is not None:
            return self._create_entry_for_model(user_input[CONF_MODEL])

        return self.async_show_form(
            step_id="model_confirm",
            data_schema=_model_schema(default=self._detected_model),
            description_placeholders={"model": self._detected_model or ""},
        )

    async def async_step_model_confirm_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually select the spa model when auto-detection fails."""
        if user_input is not None:
            return self._create_entry_for_model(user_input[CONF_MODEL])

        return self.async_show_form(
            step_id="model_confirm_manual",
            data_schema=_model_schema(default=None),
        )

    def _create_entry_for_model(self, model: str) -> ConfigFlowResult:
        """Create the config entry for the selected model."""
        return self.async_create_entry(
            title=f"Joyonway Spa ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_MODEL: model,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the spa model."""
        entry = self._get_reconfigure_entry()
        current_model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)

        if user_input is not None:
            return self.async_update_reload_and_abort(
                entry,
                data_updates={CONF_MODEL: user_input[CONF_MODEL]},
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_model_schema(default=current_model),
        )
