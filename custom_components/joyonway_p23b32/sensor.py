from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import CONF_HOST, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

SENSORS = [
    ("water_temperature", "Water temperature", "mdi:thermometer-water"),
    ("setpoint", "Setpoint", "mdi:thermometer-chevron-up"),
]

async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([JoyonwaySensor(c, entry, k, n, i) for k, n, i in SENSORS])

def _device_info(entry):
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Joyonway P23B32",
        "manufacturer": "Joyonway",
        "model": "P23B32",
        "configuration_url": f"http://{entry.data[CONF_HOST]}",
    }

class JoyonwaySensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, c, entry, key, name, icon):
        super().__init__(c)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        return self.coordinator.data.get(self._key) if self.coordinator.data else None

    @property
    def available(self):
        return self.coordinator.available
