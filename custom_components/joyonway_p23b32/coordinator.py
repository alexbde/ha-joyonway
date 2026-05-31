from datetime import timedelta
import logging
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from .const import DOMAIN, SCAN_INTERVAL
from .rs485 import read_spa_status

_LOGGER = logging.getLogger(__name__)

class JoyonwayCoordinator(DataUpdateCoordinator):
    def __init__(self, hass: HomeAssistant, host: str, port: int):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(seconds=SCAN_INTERVAL))
        self.host = host
        self.port = port
        self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def _async_update_data(self):
        data = await read_spa_status(self.host, self.port)
        if data is None:
            self._available = False
            raise UpdateFailed(f"No response from W610 {self.host}:{self.port}")
        self._available = True
        return data
