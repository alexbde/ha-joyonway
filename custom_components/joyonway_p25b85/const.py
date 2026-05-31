"""Constants for the Joyonway P25B85 integration."""
from __future__ import annotations

DOMAIN: str = "joyonway_p25b85"

# Configuration keys
CONF_HOST: str = "host"
CONF_PORT: str = "port"
CONF_MODEL: str = "model"

# Default values (override via config flow UI)
DEFAULT_HOST: str = "192.168.1.100"
DEFAULT_PORT: int = 8899
DEFAULT_MODEL: str = "P25B85"
DEFAULT_NAME: str = "Joyonway P25B85"

# RS485 behaviour
TCP_TIMEOUT: float = 5.0

# Coordinator polling interval (seconds between broadcast reads)
SCAN_INTERVAL: int = 30

# Loaded platforms (read-only: no button platform)
PLATFORMS: list[str] = ["sensor", "binary_sensor"]

