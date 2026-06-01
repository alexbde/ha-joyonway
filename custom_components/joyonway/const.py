"""Constants for the Joyonway P25B85 integration."""
from __future__ import annotations

DOMAIN: str = "joyonway"

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
DEFAULT_NAME: str = "Joyonway Spa"

# RS485 behaviour
TCP_TIMEOUT: float = 5.0
COMMAND_COOLDOWN: float = 1.0  # minimum seconds between commands

# Intent queue
INTENT_COALESCE_SECONDS: float = 0.3  # coalesce window for rapid intents
INTENT_RETRY_COUNT: int = 1  # retry once on send failure

# Coordinator fallback poll interval (health-check only; reader loop is primary)
SCAN_INTERVAL: int = 60

# Resilient UI timing
AVAILABILITY_GRACE_SECONDS: float = 10.0
RX_STALE_SECONDS: float = 15.0
OPTIMISTIC_TIMEOUT_SECONDS: float = 10.0

# Auto clock sync
CLOCK_SYNC_DRIFT_THRESHOLD: int = 30  # seconds
CLOCK_SYNC_COOLDOWN: int = 3600  # seconds between auto syncs

# Loaded platforms
PLATFORMS: list[str] = ["sensor", "binary_sensor", "switch", "fan", "climate", "time", "button"]
