"""Sensor platform for Joyonway P25B85.

Entities are driven by the model adapter's entity_descriptions().
"""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.base import SpaEntityDescription
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensor entities from config entry."""
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions = coordinator.adapter.entity_descriptions()

    entities = [
        JoyonwaySensor(coordinator, entry, desc)
        for desc in descriptions
        if desc.platform == "sensor"
    ]
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


class JoyonwaySensor(CoordinatorEntity, SensorEntity):
    """A sensor entity driven by the model adapter."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JoyonwayP25B85Coordinator,
        entry: ConfigEntry,
        description: SpaEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._key = description.key
        self._attr_name = description.name
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)
        self._attr_entity_registry_enabled_default = description.enabled_by_default

        if description.icon:
            self._attr_icon = description.icon

        # Map string device_class to HA enum
        if description.device_class == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif description.state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

        if description.entity_category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        """Return the sensor value from coordinator data."""
        if self.coordinator.data:
            return self.coordinator.data.get(self._key)
        return None

    @property
    def available(self) -> bool:
        """Return True if coordinator has valid data."""
        return self.coordinator.available

