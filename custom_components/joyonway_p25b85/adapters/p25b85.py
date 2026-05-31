"""P25B85 model adapter — byte map and entity definitions.

Byte positions from KDy's reverse engineering (HA community post #74).
All indexes are logical-frame positions (after full-frame unescape).

⚠️ Pump byte index (12 vs 13) is UNCONFIRMED — needs local capture validation.
"""
from __future__ import annotations

from .base import ModelAdapter, SpaEntityDescription

# Broadcast frame header signature for P25B85 (bytes 0-8)
# byte[8] = 0x03 distinguishes P25B85 from P23B32 (0x02)
P25B85_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])

# Byte positions in the logical (unescaped) broadcast frame
IDX_WATER_TEMP = 9  # Fahrenheit
IDX_PUMP_BYTE = 12  # ⚠️ UNCONFIRMED: could be 12 or 13. Using 12 as primary candidate.
IDX_HEATER_STATE = 15
IDX_SETPOINT = 16  # Fahrenheit
IDX_LIGHT_FLAGS = 18
IDX_UV_FLAG = 29
IDX_DATETIME_START = 53  # bytes 53-58: year, month, day, hour, minute, second

# Pump masks (from KDy status table)
MASK_PUMP_LOW = 0x02  # filtration / circulation
MASK_PUMP_HIGH = 0x04  # massage jets

# Light
MASK_LIGHT = 0x01

# UV/Ozone
MASK_UV = 0x20

# Heater state values
HEATER_OFF = 0x00
HEATER_CIRCULATION = 0x50
HEATER_HEATING = 0x54
HEATER_COOLDOWN = 0x40
HEATER_UV_OZONE = 0xC1

HEATER_STATE_MAP: dict[int, str] = {
    HEATER_OFF: "off",
    HEATER_CIRCULATION: "circulation",
    HEATER_HEATING: "heating",
    HEATER_COOLDOWN: "cooldown",
    HEATER_UV_OZONE: "uv_ozone",
}


def _fahrenheit_to_celsius(f: int) -> float | None:
    """Convert Fahrenheit to Celsius, return None for invalid values."""
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9, 1)


class P25B85Adapter:
    """Adapter for the Joyonway P25B85 controller (read-only)."""

    model: str = "P25B85"
    broadcast_signature: bytes = P25B85_SIGNATURE
    unescape_full_frame: bool = True
    supports_writes: bool = False

    def parse_status(self, frame: bytes) -> dict | None:
        """Extract state dict from an unescaped broadcast frame.

        Returns None if frame doesn't match P25B85 signature or is too short.
        """
        if len(frame) < 30:
            return None
        # Check signature (first 9 bytes)
        if frame[: len(self.broadcast_signature)] != self.broadcast_signature:
            return None

        water_temp_f = frame[IDX_WATER_TEMP]
        setpoint_f = frame[IDX_SETPOINT]
        pump_byte = frame[IDX_PUMP_BYTE]
        heater_byte = frame[IDX_HEATER_STATE]
        light_byte = frame[IDX_LIGHT_FLAGS]
        uv_byte = frame[IDX_UV_FLAG]

        heater_state = HEATER_STATE_MAP.get(heater_byte, "unknown")

        result: dict = {
            "water_temperature": _fahrenheit_to_celsius(water_temp_f),
            "setpoint": _fahrenheit_to_celsius(setpoint_f),
            "pump_low": bool(pump_byte & MASK_PUMP_LOW),
            "pump_high": bool(pump_byte & MASK_PUMP_HIGH),
            "light": bool(light_byte & MASK_LIGHT),
            "heater_active": heater_byte == HEATER_HEATING,
            "heater_state": heater_state,
            "uv_lamp": heater_byte == HEATER_UV_OZONE or bool(uv_byte & MASK_UV),
            # Raw diagnostic values
            "raw_pump_byte": pump_byte,
            "raw_heater_byte": heater_byte,
            "raw_light_byte": light_byte,
            "raw_uv_byte": uv_byte,
            "raw_water_temp_f": water_temp_f,
            "raw_setpoint_f": setpoint_f,
        }

        # Parse datetime if frame is long enough
        if len(frame) > IDX_DATETIME_START + 5:
            dt_bytes = frame[IDX_DATETIME_START : IDX_DATETIME_START + 6]
            try:
                result["spa_datetime"] = (
                    f"20{dt_bytes[0]:02d}-{dt_bytes[1]:02d}-{dt_bytes[2]:02d} "
                    f"{dt_bytes[3]:02d}:{dt_bytes[4]:02d}:{dt_bytes[5]:02d}"
                )
            except (ValueError, IndexError):
                result["spa_datetime"] = None
        else:
            result["spa_datetime"] = None

        return result

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return entity descriptions for P25B85."""
        return _P25B85_ENTITIES


_P25B85_ENTITIES: list[SpaEntityDescription] = [
    # Sensors
    SpaEntityDescription(
        platform="sensor",
        key="water_temperature",
        name="Water temperature",
        icon="mdi:thermometer-water",
        device_class="temperature",
        state_class="measurement",
        native_unit="°C",
    ),
    SpaEntityDescription(
        platform="sensor",
        key="setpoint",
        name="Setpoint",
        icon="mdi:thermometer-chevron-up",
        device_class="temperature",
        state_class="measurement",
        native_unit="°C",
    ),
    SpaEntityDescription(
        platform="sensor",
        key="heater_state",
        name="Heater state",
        icon="mdi:fire",
    ),
    SpaEntityDescription(
        platform="sensor",
        key="spa_datetime",
        name="Spa clock",
        icon="mdi:clock-outline",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    # Binary sensors
    SpaEntityDescription(
        platform="binary_sensor",
        key="pump_low",
        name="Pump low (filtration)",
        icon="mdi:pump",
    ),
    SpaEntityDescription(
        platform="binary_sensor",
        key="pump_high",
        name="Pump high (jets)",
        icon="mdi:pump",
    ),
    SpaEntityDescription(
        platform="binary_sensor",
        key="light",
        name="Light",
        icon="mdi:lightbulb",
    ),
    SpaEntityDescription(
        platform="binary_sensor",
        key="heater_active",
        name="Heater active",
        icon="mdi:fire",
        device_class="heat",
    ),
    SpaEntityDescription(
        platform="binary_sensor",
        key="uv_lamp",
        name="UV lamp",
        icon="mdi:lightbulb-fluorescent-tube",
    ),
    # Diagnostic raw values (disabled by default)
    SpaEntityDescription(
        platform="sensor",
        key="raw_pump_byte",
        name="Raw pump byte",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="raw_heater_byte",
        name="Raw heater byte",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
]

