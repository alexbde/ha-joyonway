"""Constants for the Joyonway P23B32 integration."""
from __future__ import annotations
DOMAIN: str = "joyonway_p23b32"
# Configuration keys
CONF_HOST: str = "host"
CONF_PORT: str = "port"
# Default values (USR-W610 standard)
DEFAULT_HOST: str = "192.168.1.34"
DEFAULT_PORT: int = 8899
DEFAULT_NAME: str = "Joyonway P23B32"
# RS485 behaviour
REPEAT_COUNT: int = 10
REPEAT_INTERVAL: float = 0.5
TCP_TIMEOUT: float = 5.0
# Coordinator polling - broadcast read every X seconds
SCAN_INTERVAL: int = 40
# Confirmed command identifiers
CMD_LUMIERE_ON: str = "lumiere_on"
CMD_LUMIERE_OFF: str = "lumiere_off"
CMD_POMPE_GAUCHE_ON: str = "pompe_gauche_on"
CMD_POMPE_GAUCHE_OFF: str = "pompe_gauche_off"
CMD_POMPE_DROITE_ON: str = "pompe_droite_on"
CMD_POMPE_DROITE_OFF: str = "pompe_droite_off"
CMD_BULLEUR_ON: str = "bulleur_on"
CMD_BULLEUR_OFF: str = "bulleur_off"
CMD_FILTRATION: str = "filtration"
CMD_ALL_OFF: str = "all_off"
CMD_CONSIGNE: str = "consigne"
# Loaded platforms
PLATFORMS: list[str] = ["button", "sensor", "binary_sensor"]
