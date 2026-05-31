# 2026-05-13 | button platform | Exposes all confirmed RS485 commands | Depends: rs485.py, __init__.py
"""Joyonway P23B32 buttons (one-shot RS485 commands)."""
from __future__ import annotations
import logging
from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import (
    CMD_ALL_OFF, CMD_BULLEUR_OFF, CMD_BULLEUR_ON,
    CMD_FILTRATION, CMD_LUMIERE_OFF, CMD_LUMIERE_ON,
    CMD_POMPE_DROITE_OFF, CMD_POMPE_DROITE_ON,
    CMD_POMPE_GAUCHE_OFF, CMD_POMPE_GAUCHE_ON,
    DOMAIN,
)
from .rs485 import send_command

_LOGGER = logging.getLogger(__name__)

# (command_key, UI label, mdi icon)
BUTTONS: list[tuple[str, str, str]] = [
    (CMD_LUMIERE_ON,       "Light ON",         "mdi:lightbulb-on"),
    (CMD_LUMIERE_OFF,      "Light OFF",         "mdi:lightbulb-off"),
    (CMD_POMPE_GAUCHE_ON,  "Left jets ON",     "mdi:pump"),
    (CMD_POMPE_GAUCHE_OFF, "Left jets OFF",    "mdi:pump-off"),
    (CMD_POMPE_DROITE_ON,  "Right jets ON",    "mdi:pump"),
    (CMD_POMPE_DROITE_OFF, "Right jets OFF",   "mdi:pump-off"),
    (CMD_BULLEUR_ON,       "Blower ON",        "mdi:chart-bubble"),
    (CMD_BULLEUR_OFF,      "Blower OFF",       "mdi:chart-bubble"),
    (CMD_FILTRATION,       "Filtration",       "mdi:filter"),
    (CMD_ALL_OFF,          "All OFF",          "mdi:power-off"),
]

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Joyonway buttons from a config entry."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    host: str = coordinator.host
    port: int = coordinator.port
    entities = [
        JoyonwayButton(entry.entry_id, host, port, cmd, label, icon)
        for cmd, label, icon in BUTTONS
    ]
    async_add_entities(entities)

class JoyonwayButton(ButtonEntity):
    """One-shot button for a Joyonway command."""
    _attr_has_entity_name = True

    def __init__(self, entry_id, host, port, command, label, icon):
        self._entry_id = entry_id
        self._host = host
        self._port = port
        self._command = command
        self._attr_name = label
        self._attr_icon = icon
        self._attr_unique_id = f"{entry_id}_{command}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="Joyonway P23B32",
            manufacturer="Joyonway",
            model="P23B32",
        )

    async def async_press(self) -> None:
        """Send the RS485 command."""
        ok = await send_command(self._host, self._port, self._command)
        if not ok:
            _LOGGER.warning("Failed to send command %s", self._command)
