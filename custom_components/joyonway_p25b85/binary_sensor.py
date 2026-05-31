"""Binary sensor platform for Joyonway P25B85.

Entities are driven by the model adapter's entity_descriptions().
Includes a bridge connectivity sensor.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.base import SpaEntityDescription
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensor entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions = coordinator.adapter.entity_descriptions()

    entities: list[BinarySensorEntity] = [
        JoyonwayBinarySensor(coordinator, entry, desc)
        for desc in descriptions
        if desc.platform == "binary_sensor"
    ]
    # Always add bridge connectivity sensor
    entities.append(JoyonwayBridgeConnectivity(coordinator, entry))

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> dict:
    """Build device info dict."""
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Joyonway P25B85",
        "manufacturer": "Joyonway",
        "model": "P25B85",
        "configuration_url": f"http://{entry.data[CONF_HOST]}",
    }


class JoyonwayBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """A binary sensor entity driven by the model adapter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
        description: SpaEntityDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self._key = description.key
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)
        self._attr_entity_registry_enabled_default = description.enabled_by_default

        if description.icon:
            self._attr_icon = description.icon

        if description.device_class == "heat":
            self._attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._key, False)
        return None

    @property
    def available(self) -> bool:
        """Return True if coordinator has valid data."""
        return self.coordinator.available


class JoyonwayBridgeConnectivity(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor showing bridge TCP connectivity."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "RS485 bridge connection"
    _attr_icon = "mdi:wifi-check"

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_bridge_connectivity"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool:
        """Return True if bridge is reachable."""
        return self.coordinator.available

