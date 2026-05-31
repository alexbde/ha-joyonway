"""Joyonway P25B85 integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST, CONF_MODEL, CONF_PORT, DEFAULT_MODEL, DOMAIN,
    OPT_OZONE_MODE, OZONE_MODE_AUTO, PLATFORMS,
)
from .coordinator import JoyonwayP25B85Coordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Joyonway P25B85 from a config entry."""
    host = entry.data[CONF_HOST]
    port = entry.data[CONF_PORT]
    model = entry.data.get(CONF_MODEL, DEFAULT_MODEL)

    coordinator = JoyonwayP25B85Coordinator(hass, host, port, model, entry)
    await coordinator.async_setup()
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — sync ozone mode to spa, then reload."""
    coordinator: JoyonwayP25B85Coordinator = entry.runtime_data
    new_mode = entry.options.get(OPT_OZONE_MODE, OZONE_MODE_AUTO)

    # Keep the controller mode aligned immediately when the option changes,
    # then reload so entities/options reflect the new configuration.
    if new_mode != coordinator.last_detected_ozone_mode:

        def _build_ozone_mode(overrides: dict, data: dict | None) -> bytes | None:
            return coordinator.adapter.build_ozone_mode_command(overrides["mode"])

        coordinator.intent_queue.submit(
            group="ozone_mode",
            overrides={"mode": new_mode},
            build_fn=_build_ozone_mode,
            on_failure=lambda: _LOGGER.error(
                "Ozone mode: failed to send '%s' command", new_mode
            ),
        )
        await coordinator.intent_queue.flush()
        _LOGGER.info("Ozone mode: queue flushed for '%s' before reload", new_mode)

    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN].pop(
            entry.entry_id, None
        )
        if coordinator is not None:
            await coordinator.async_shutdown()
    else:
        _LOGGER.warning("Unload failed; coordinator stays active")
    return unload_ok
