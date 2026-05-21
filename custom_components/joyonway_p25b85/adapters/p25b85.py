"""P25B85 model adapter — byte map and entity definitions.

Byte positions validated against local RS485 captures.
All indexes are 0-based logical-frame positions (after full-frame unescape).

KDy's HA community post #74 used 1-based byte numbering, which caused an
off-by-one when we originally transcribed the byte map. KDy's data is fully
consistent with our captures once the indexing is corrected:
  KDy "byte 13" → 0-based byte 12 (pump)
  KDy "byte 15" → 0-based byte 14 (heater state)
  KDy "byte 18" → 0-based byte 17 (light flags)
  KDy "byte 28" → 0-based byte 27 (pump mirror)
  KDy "byte 29" → 0-based byte 28 (activity flag)

Capture validation summary:
  - Byte 12: pump (0x02=low, 0x04=high) ✅ confirmed (matches KDy)
  - Byte 14: heater state ✅ confirmed (KDy's "byte 15", 1-based)
  - Byte 17: light flags ✅ confirmed (KDy's "byte 18", 1-based)
  - Byte 28: activity flag ✅ confirmed (KDy's "byte 29", 1-based)
    Set during both heating and UV/ozone, so it is not UV-specific.
  - Byte 27: mirrors byte 12 (pump), not used
  - Byte 13: static in local captures, not pump data
  - Byte 15: static in local captures, not heater state
"""
from __future__ import annotations

from datetime import datetime, timezone

try:
    from homeassistant.util import dt as dt_util
except ImportError:  # standalone / test usage without HA
    dt_util = None  # type: ignore[assignment]

from .base import SpaEntityDescription

# Broadcast frame header signature for P25B85 (bytes 0-8)
# byte[8] = 0x03 distinguishes P25B85 from P23B32 (0x02)
P25B85_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])

# Byte positions in the logical (unescaped) broadcast frame (0-based)
IDX_WATER_TEMP = 9   # Fahrenheit
IDX_PUMP_BYTE = 12   # ✅ confirmed: 0x02=low, 0x04=high (KDy "byte 13")
IDX_HEATER_STATE = 14  # ✅ confirmed (KDy "byte 15")
IDX_SETPOINT = 16    # Fahrenheit
IDX_LIGHT_FLAGS = 17   # ✅ confirmed (KDy "byte 18")
IDX_ACTIVITY_FLAG = 28  # ✅ confirmed (KDy "byte 29"); set during heating and UV/ozone
# Legacy alias kept for raw diagnostics/backward compatibility.
IDX_UV_FLAG = IDX_ACTIVITY_FLAG
IDX_DATETIME_START = 53  # bytes 53-58: year, month, day, hour, minute, second

# Pump masks
MASK_PUMP_LOW = 0x02   # filtration / circulation ✅
MASK_PUMP_HIGH = 0x04  # massage jets ✅

# Light
MASK_LIGHT = 0x01  # ✅ bit 0 at byte 17

# Activity flag at byte 28 (not UV-specific; use heater byte for UV detection)
MASK_ACTIVITY = 0x20
# Legacy alias kept for callers that imported the old name.
MASK_UV = MASK_ACTIVITY

# Heater state values (at byte 14)
# KDy describes three heating stages: circulation → heating → cooldown/off
# Our captures confirm 0x40 and 0x50; heating and UV differ by 1 bit
# from KDy's values (firmware variant or sub-state). Both sets are mapped.
HEATER_OFF = 0x40    # Idle/off (KDy called this "cooldown") ✅ confirmed
HEATER_CIRCULATION = 0x50  # Circulation pump pre-heating (KDy: "circulation") ✅ confirmed
HEATER_HEATING = 0x55     # Actively heating (our capture) ✅ confirmed
HEATER_HEATING_ALT = 0x54  # Actively heating (KDy's value, differs by bit 0)
HEATER_DISINFECTION = 0x41    # Scheduled disinfection cycle (our capture) ✅ confirmed
HEATER_DISINFECTION_ALT = 0xC1  # KDy variant (differs by bit 7)

HEATER_STATE_MAP: dict[int, str] = {
    HEATER_OFF: "off",
    HEATER_CIRCULATION: "circulation",
    HEATER_HEATING: "heating",
    HEATER_HEATING_ALT: "heating",      # KDy variant
    HEATER_DISINFECTION: "disinfection",
    HEATER_DISINFECTION_ALT: "disinfection",   # KDy variant
}

# ──────────────────────────────────────────────────────────────
# Command frames (captured from PB554 panel, replay-only)
# CRC algorithm is proprietary; we ONLY replay verbatim frames.
# ──────────────────────────────────────────────────────────────

# Light toggle — same frame for ON and OFF (it's a toggle)
CMD_LIGHT_TOGGLE = bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d")

# Pump transitions (must match current state → target state)
CMD_PUMP_OFF_TO_LOW = bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d")
CMD_PUMP_LOW_TO_HIGH = bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d")
CMD_PUMP_HIGH_TO_OFF = bytes.fromhex("1a0120103ca110a10400000000c0005600735738e91d")

# Pump state → next command mapping for cycling
PUMP_CYCLE_MAP: dict[str, tuple[bytes, str]] = {
    "off": (CMD_PUMP_OFF_TO_LOW, "low"),
    "low": (CMD_PUMP_LOW_TO_HIGH, "high"),
    "high": (CMD_PUMP_HIGH_TO_OFF, "off"),
}

# Temperature setpoint command frames (captured from PB554 panel).
# Keys are target °C; values are raw wire-format hex frames (replay verbatim).
# 31 frames covering 10°C (50°F) to 40°C (104°F) in 1°C steps.
# The °C→°F mapping follows the panel's +1,+2,+2,+2,+2 repeating pattern.
TEMP_COMMAND_TABLE: dict[int, bytes] = {
    10: bytes.fromhex("1a0120103ca110a10000809800c0003200cb80efa11d"),   # 50°F
    11: bytes.fromhex("1a0120103ca110a10000808800c000330080db45461d"),   # 51°F
    12: bytes.fromhex("1a0120103ca110a10000808800c0003500923096421d"),   # 53°F
    13: bytes.fromhex("1a0120103ca110a10000808800c00037009c6927411d"),   # 55°F
    14: bytes.fromhex("1a0120103ca110a10000808800c0003900b6e6314b1d"),   # 57°F
    15: bytes.fromhex("1a0120103ca110a10000808800c0003b00b8bf80481d"),   # 59°F
    16: bytes.fromhex("1a0120103ca110a10000808800c0003c002df88b4d1d"),   # 60°F
    17: bytes.fromhex("1a0120103ca110a10000808800c0003e0023a13a4e1d"),   # 62°F
    18: bytes.fromhex("1a0120103ca110a10000808800c0004000595798141d"),   # 64°F
    19: bytes.fromhex("1a0120103ca110a10000808800c0004200570e29171d"),   # 66°F
    20: bytes.fromhex("1a0120103ca110a10000808800c000440045e5fa131d"),   # 68°F
    21: bytes.fromhex("1a0120103ca110a10000808800c0004500c24922121d"),   # 69°F
    22: bytes.fromhex("1a0120103ca110a10000808800c0004700cc1093111d"),   # 71°F
    23: bytes.fromhex("1a0120103ca110a10000808800c0004900e69f851b0b1d"),  # 73°F (escaped CRC)
    24: bytes.fromhex("1a0120103ca110a10000808800c0004b00e8c634181d"),   # 75°F
    25: bytes.fromhex("1a0120103ca110a10000809800c0004d0036da95fa1d"),   # 77°F
    26: bytes.fromhex("1a0120103ca110a10000809800c0004e00bf2ffcf81d"),   # 78°F
    27: bytes.fromhex("1a0120103ca110a10000808800c0005000299f12091d"),   # 80°F
    28: bytes.fromhex("1a0120103ca110a10000808800c000520027c6a30a1d"),   # 82°F
    29: bytes.fromhex("1a0120103ca110a10000808800c0005400352d700e1d"),   # 84°F
    30: bytes.fromhex("1a0120103ca110a10000808800c00056003b74c10d1d"),   # 86°F
    31: bytes.fromhex("1a0120103ca110a10000808800c0005700bcd8190c1d"),   # 87°F
    32: bytes.fromhex("1a0120103ca110a10000809900c00059004bc82aaf1d"),   # 89°F
    33: bytes.fromhex("1a0120103ca110a10000809900c0005b0045919bac1d"),   # 91°F
    34: bytes.fromhex("1a0120103ca110a10000809900c0005d00577a48a81d"),   # 93°F
    35: bytes.fromhex("1a0120103ca110a10000809900c0005f005923f9ab1d"),   # 95°F
    36: bytes.fromhex("1a0120103ca110a10000809900c00060006458a8861d"),   # 96°F
    37: bytes.fromhex("1a0120103ca110a10000809900c00062006a0119851d"),   # 98°F
    38: bytes.fromhex("1a0120103ca110a10000809900c000640078eaca811d"),   # 100°F
    39: bytes.fromhex("1a0120103ca110a10000809900c000660076b37b821d"),   # 102°F
    40: bytes.fromhex("1a0120103ca110a10000809900c00068005c3c6d881d"),   # 104°F
}

TEMP_MIN_C = 10
TEMP_MAX_C = 40


def _fahrenheit_to_celsius(f: int) -> int | None:
    """Convert Fahrenheit to Celsius, return None for invalid values.

    Returns an integer because the spa panel only displays whole-degree
    values; the extra decimal from °F→°C conversion is false precision.
    """
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9)


class P25B85Adapter:
    """Adapter for the Joyonway P25B85 controller."""

    model: str = "P25B85"
    broadcast_signature: bytes = P25B85_SIGNATURE
    unescape_full_frame: bool = True
    supports_writes: bool = True

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
        activity_byte = frame[IDX_UV_FLAG]

        heater_state = HEATER_STATE_MAP.get(heater_byte, "unknown")

        # Derive pump state string
        if pump_byte & MASK_PUMP_HIGH:
            pump_state = "high"
        elif pump_byte & MASK_PUMP_LOW:
            pump_state = "low"
        else:
            pump_state = "off"

        result: dict = {
            "water_temperature": _fahrenheit_to_celsius(water_temp_f),
            "setpoint": _fahrenheit_to_celsius(setpoint_f),
            "pump_low": bool(pump_byte & MASK_PUMP_LOW),
            "pump_high": bool(pump_byte & MASK_PUMP_HIGH),
            "pump_state": pump_state,
            "light": bool(light_byte & MASK_LIGHT),
            "heater_active": heater_byte in (HEATER_HEATING, HEATER_HEATING_ALT),
            "heater_state": heater_state,
            "disinfection_active": heater_byte in (HEATER_DISINFECTION, HEATER_DISINFECTION_ALT),
            # Raw diagnostic values
            "raw_pump_byte": pump_byte,
            "raw_heater_byte": heater_byte,
            "raw_light_byte": light_byte,
            "raw_activity_byte": activity_byte,
            "raw_water_temp_f": water_temp_f,
            "raw_setpoint_f": setpoint_f,
        }

        # Parse datetime if frame is long enough.
        # The controller clock sends local time without timezone info.
        # We attach the HA instance timezone so the timestamp sensor displays
        # the value as-is without any UTC offset conversion.
        if len(frame) > IDX_DATETIME_START + 5:
            dt_bytes = frame[IDX_DATETIME_START : IDX_DATETIME_START + 6]
            try:
                local_tz = dt_util.DEFAULT_TIME_ZONE if dt_util else timezone.utc
                result["spa_datetime"] = datetime(
                    year=2000 + dt_bytes[0],
                    month=dt_bytes[1],
                    day=dt_bytes[2],
                    hour=dt_bytes[3],
                    minute=dt_bytes[4],
                    second=dt_bytes[5],
                    tzinfo=local_tz,
                )
            except (ValueError, IndexError):
                result["spa_datetime"] = None
        else:
            result["spa_datetime"] = None

        return result

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return entity descriptions for P25B85."""
        return _P25B85_ENTITIES

    def get_pump_state(self, data: dict) -> str:
        """Return current pump state as 'off', 'low', or 'high'."""
        if data.get("pump_high"):
            return "high"
        if data.get("pump_low"):
            return "low"
        return "off"

    def get_pump_command(self, current_state: str, target_state: str) -> bytes | None:
        """Return command frame to transition pump from current to target state.

        Returns None if transition is not directly possible (need intermediate steps).
        """
        if current_state == target_state:
            return None

        # Direct transitions
        transitions = {
            ("off", "low"): CMD_PUMP_OFF_TO_LOW,
            ("low", "high"): CMD_PUMP_LOW_TO_HIGH,
            ("high", "off"): CMD_PUMP_HIGH_TO_OFF,
        }
        return transitions.get((current_state, target_state))

    def get_pump_cycle_command(self, data: dict) -> bytes | None:
        """Return command to advance pump to next state in cycle."""
        current = self.get_pump_state(data)
        entry = PUMP_CYCLE_MAP.get(current)
        return entry[0] if entry else None

    def get_temp_command(self, target_celsius: int) -> bytes | None:
        """Return the command frame for a target temperature in °C.

        Returns None if the temperature is out of range (10-40°C).
        """
        return TEMP_COMMAND_TABLE.get(target_celsius)


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
        key="heater_state",
        name="Heater state",
        icon="mdi:fire",
        device_class="enum",
        options=["off", "circulation", "heating", "disinfection", "unknown"],
    ),
    SpaEntityDescription(
        platform="sensor",
        key="pump_state",
        name="Pump state",
        icon="mdi:pump",
        device_class="enum",
        options=["off", "low", "high"],
    ),
    SpaEntityDescription(
        platform="sensor",
        key="spa_datetime",
        name="Spa clock",
        icon="mdi:clock-outline",
        device_class="timestamp",
        entity_category="diagnostic",
        enabled_by_default=False,
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
