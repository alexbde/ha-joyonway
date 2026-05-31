from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import CONF_HOST
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

# (key, name, icon, device_class)
BS = [
    ("filtration",   "Filtration",        "mdi:pump",          None),
    ("pompe_gauche", "Left jets pump",     "mdi:pump",          None),
    ("pompe_droite", "Right jets pump",    "mdi:turbine",       None),
    ("bulleur",      "Blower",             "mdi:weather-windy", None),
    ("lumiere",      "Light",              "mdi:lightbulb",     None),
    ("chauffage",    "Heater",             "mdi:fire",          BinarySensorDeviceClass.HEAT),
]

def _device_info(entry):
    return {
        "identifiers": {(DOMAIN, entry.entry_id)},
        "name": "Joyonway P23B32",
        "manufacturer": "Joyonway",
        "model": "P23B32",
        "configuration_url": f"http://{entry.data[CONF_HOST]}",
    }

async def async_setup_entry(hass, entry, async_add_entities):
    c = hass.data[DOMAIN][entry.entry_id]
    entities = [JoyonwayBS(c, entry, k, n, i, d) for k, n, i, d in BS]
    entities.append(JoyonwayConn(c, entry))
    async_add_entities(entities)

class JoyonwayBS(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, c, entry, key, name, icon, dc):
        super().__init__(c)
        self._key = key
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_class = dc
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self):
        return self.coordinator.data.get(self._key, False) if self.coordinator.data else None

    @property
    def available(self):
        return self.coordinator.available

class JoyonwayConn(CoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "W610 connection"
    _attr_icon = "mdi:wifi-check"

    def __init__(self, c, entry):
        super().__init__(c)
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self):
        return self.coordinator.available
