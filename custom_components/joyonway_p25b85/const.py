"""Constants for the Joyonway P25B85 integration."""
from __future__ import annotations

DOMAIN: str = "joyonway_p25b85"

# Configuration keys
CONF_HOST: str = "host"
CONF_PORT: str = "port"
CONF_MODEL: str = "model"

# Options keys
OPT_OZONE_MODE: str = "ozone_mode"
OPT_AUTO_SYNC_CLOCK: str = "auto_sync_clock"

# Option values
OZONE_MODE_AUTO: str = "auto"
OZONE_MODE_MANUAL: str = "manual"

# Default values (override via config flow UI)
DEFAULT_HOST: str = "192.168.1.100"
DEFAULT_PORT: int = 8899
DEFAULT_MODEL: str = "P25B85"
DEFAULT_NAME: str = "Joyonway P25B85"

# RS485 behaviour
TCP_TIMEOUT: float = 5.0

# Coordinator polling interval (seconds between broadcast reads)
SCAN_INTERVAL: int = 30

# Auto clock sync
CLOCK_SYNC_DRIFT_THRESHOLD: int = 30  # seconds
CLOCK_SYNC_COOLDOWN: int = 3600  # seconds between auto syncs

# Loaded platforms
PLATFORMS: list[str] = ["sensor", "binary_sensor", "switch", "fan", "climate", "time", "button"]

