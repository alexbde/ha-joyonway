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
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .adapters.base import SpaEntityDescription
from .const import DOMAIN
from .coordinator import JoyonwayP25B85Coordinator
from .entity import device_info


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
        self._attr_translation_key = description.key
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = device_info(entry)
        self._attr_entity_registry_enabled_default = description.enabled_by_default
        self._icon_map = description.icon_map
        self._default_icon = description.icon

        if description.icon and not description.icon_map:
            self._attr_icon = description.icon

        # Map string device_class to HA enum
        if description.device_class == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif description.device_class == "enum":
            self._attr_device_class = SensorDeviceClass.ENUM
            if description.options:
                self._attr_options = description.options
        elif description.device_class == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif description.state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

        if description.entity_category == "diagnostic":
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def icon(self) -> str | None:
        """Return icon based on current state if icon_map is defined."""
        if self._icon_map and self.coordinator.data:
            value = self.coordinator.data.get(self._key)
            if value in self._icon_map:
                return self._icon_map[value]
        return self._default_icon

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

