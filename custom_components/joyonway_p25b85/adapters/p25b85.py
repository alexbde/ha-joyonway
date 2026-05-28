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

# Schedule byte positions in broadcast frame
# Layout per schedule: [s1_start_h] [s1_start_m] [s1_end_h] [s1_end_m]
#                      [s2_start_h] [s2_start_m] [s2_end_h] [s2_end_m]
# Start-hour bytes encode: hour | 0x40 when slot is enabled, plain hour when disabled
MASK_SLOT_ENABLED = 0x40   # bit 6 on start-hour byte = slot enabled
MASK_SLOT_HOUR = 0x3F      # lower 6 bits = hour value (0-23)

# Heat schedule: broadcast bytes 19-26
IDX_HEAT_SLOT1_START_H = 19   # Heat slot 1 start hour (+ enable flag)
IDX_HEAT_SLOT1_START_M = 20   # Heat slot 1 start minute
IDX_HEAT_SLOT1_END_H = 21     # Heat slot 1 end hour
IDX_HEAT_SLOT1_END_M = 22     # Heat slot 1 end minute
IDX_HEAT_SLOT2_START_H = 23   # Heat slot 2 start hour (+ enable flag)
IDX_HEAT_SLOT2_START_M = 24   # Heat slot 2 start minute
IDX_HEAT_SLOT2_END_H = 25     # Heat slot 2 end hour
IDX_HEAT_SLOT2_END_M = 26     # Heat slot 2 end minute

# Filter schedule: broadcast bytes 29-36
IDX_FILTER_SLOT1_START_H = 29  # Filter slot 1 start hour (+ enable flag)
IDX_FILTER_SLOT1_START_M = 30  # Filter slot 1 start minute
IDX_FILTER_SLOT1_END_H = 31   # Filter slot 1 end hour
IDX_FILTER_SLOT1_END_M = 32   # Filter slot 1 end minute
IDX_FILTER_SLOT2_START_H = 33  # Filter slot 2 start hour (+ enable flag)
IDX_FILTER_SLOT2_START_M = 34  # Filter slot 2 start minute
IDX_FILTER_SLOT2_END_H = 35   # Filter slot 2 end hour
IDX_FILTER_SLOT2_END_M = 36   # Filter slot 2 end minute

# Schedule command flags byte (byte 7 of command payload)
# Phase 6 finding: flags byte encodes slot enable state, NOT schedule type.
# The same encoding is used for both heat (0xA3) and filter (0xA4) commands.
# Encoding uses 2-bit pairs per slot (not single-bit flags).
# Verified against Phase 6 captures:
#   0xAA = both enabled         (heat_schedule_enable, filter_schedule_enable)
#   0x62 = s1 on, s2 off        (heat_schedule_disable, filter_schedule_disable)
#   0x9A = s1 off, s2 on        (heat_schedule_change)
#   0x52 = both off             (computed: 0xAA ^ 0xC8 ^ 0x30)
SCHED_FLAGS_TABLE: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x62,
    (False, True): 0x9A,
    (False, False): 0x52,
}

# Pump masks
MASK_PUMP_LOW = 0x02   # filtration / circulation ✅
MASK_PUMP_HIGH = 0x04  # massage jets ✅

# Light
MASK_LIGHT = 0x01  # ✅ bit 0 at byte 17

# Activity flag at byte 28 (not UV-specific; use heater byte for UV detection)
MASK_ACTIVITY = 0x20
# Legacy alias kept for callers that imported the old name.
MASK_UV = MASK_ACTIVITY

# Blower flag at byte 28 (bit 3)
MASK_BLOWER = 0x08

# Heater state values (at byte 14)
# KDy describes three heating stages: circulation → heating → cooldown/off
# Our captures confirm 0x40 and 0x50; heating and UV differ by 1 bit
# from KDy's values (firmware variant or sub-state). Both sets are mapped.
#
# Bit 3 (0x08) is the blower flag — it is ORed onto the heater byte when
# the blower is active. We strip it before lookup so every heater state
# works correctly regardless of blower state.
MASK_HEATER_BLOWER = 0x08  # bit 3 on heater byte = blower running

HEATER_OFF = 0x40    # Idle/off (KDy called this "cooldown") ✅ confirmed
HEATER_BLOWER = 0x48       # Blower active (0x40 + bit 3) ✅ Phase 6 confirmed
HEATER_CIRCULATION = 0x50  # Circulation pump pre-heating (KDy: "circulation") ✅ confirmed
HEATER_HEATING_STANDBY = 0x51  # Heating standby (circ started, heater engaging) ✅ Phase 6
HEATER_HEATING = 0x55     # Actively heating (our capture) ✅ confirmed
HEATER_HEATING_ALT = 0x54  # Actively heating (KDy's value, differs by bit 0)
HEATER_OZONE = 0x41          # Ozone cycle — scheduled (our capture) ✅ confirmed
HEATER_OZONE_ALT = 0xC1     # Ozone cycle — manual / KDy variant ✅ Phase 6

HEATER_STATE_MAP: dict[int, str] = {
    HEATER_OFF: "off",
    HEATER_CIRCULATION: "circulation",
    HEATER_HEATING_STANDBY: "heating",  # about to heat → report as heating
    HEATER_HEATING: "heating",
    HEATER_HEATING_ALT: "heating",      # KDy variant
    HEATER_OZONE: "ozone",
    HEATER_OZONE_ALT: "ozone",         # KDy variant / manual ozone
}

# ──────────────────────────────────────────────────────────────
# Command payload constants
# All commands are built dynamically via build_frame() + CRC.
# Payload layout (16 bytes): see docs/protocol.md §4.1
# ──────────────────────────────────────────────────────────────

# Common command header (bytes 0-6, shared across all type-0xA1 commands)
_CMD_HEADER = bytes([0x01, 0x20, 0x10, 0x3C, 0xA1, 0x10, 0xA1])

# Pump transition encodings — (pump_b7, pump_b8)
# Captured transitions: off→low, low→high, high→off (panel button cycle).
# Additional direct transitions use the same target-state bytes — the
# controller appears to accept any transition regardless of current state.
# Live test confirmed: off→low ✅, off→high ✅, low→off ✅ (session 2).
# low→high failed in one test run — suspected timing/bus collision issue,
# not a protocol problem (the panel successfully cycles off→low→high).
_PUMP_TRANSITIONS: dict[tuple[str, str], tuple[int, int]] = {
    ("off", "low"):   (0x02, 0x02),
    ("off", "high"):  (0x06, 0x04),
    ("low", "high"):  (0x06, 0x04),
    ("low", "off"):   (0x04, 0x00),
    ("high", "off"):  (0x04, 0x00),
    ("high", "low"):  (0x02, 0x02),
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


def _celsius_to_fahrenheit(c: int) -> int:
    """Convert Celsius to Fahrenheit (integer, standard rounding)."""
    return round(c * 9 / 5 + 32)


class P25B85Adapter:
    """Adapter for the Joyonway P25B85 controller.

    All command frames are built dynamically using the cracked CRC-32.
    No replay-only frames — every command is computed from payload + CRC.
    """

    model: str = "P25B85"
    broadcast_signature: bytes = P25B85_SIGNATURE
    unescape_full_frame: bool = True
    supports_writes: bool = True

    # ── Broadcast parsing ─────────────────────────────────────

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

        # Bit 3 of the heater byte is the blower flag — strip it so the
        # status lookup works regardless of whether the blower is running.
        heater_base = heater_byte & ~MASK_HEATER_BLOWER
        status = HEATER_STATE_MAP.get(heater_base, "unknown")

        # Derive jets state string
        if pump_byte & MASK_PUMP_HIGH:
            jets = "high"
        elif pump_byte & MASK_PUMP_LOW:
            jets = "low"
        else:
            jets = "off"

        result: dict = {
            "water_temperature": _fahrenheit_to_celsius(water_temp_f),
            "setpoint": _fahrenheit_to_celsius(setpoint_f),
            "pump_low": bool(pump_byte & MASK_PUMP_LOW),
            "pump_high": bool(pump_byte & MASK_PUMP_HIGH),
            "jets": jets,
            "light": bool(light_byte & MASK_LIGHT),
            "heater_active": heater_base in (HEATER_HEATING, HEATER_HEATING_ALT),
            "status": status,
            "heater_byte": heater_byte,
            "ozone_active": heater_base in (HEATER_OZONE, HEATER_OZONE_ALT),
            "blower": bool(activity_byte & MASK_BLOWER),
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

        # Parse heat schedule from broadcast (bytes 19-26)
        if len(frame) > IDX_HEAT_SLOT2_END_M:
            raw_s1 = frame[IDX_HEAT_SLOT1_START_H]
            raw_s2 = frame[IDX_HEAT_SLOT2_START_H]
            result["heat_slot1_start"] = (raw_s1 & MASK_SLOT_HOUR, frame[IDX_HEAT_SLOT1_START_M])
            result["heat_slot1_end"] = (frame[IDX_HEAT_SLOT1_END_H], frame[IDX_HEAT_SLOT1_END_M])
            result["heat_slot1_enabled"] = bool(raw_s1 & MASK_SLOT_ENABLED)
            result["heat_slot2_start"] = (raw_s2 & MASK_SLOT_HOUR, frame[IDX_HEAT_SLOT2_START_M])
            result["heat_slot2_end"] = (frame[IDX_HEAT_SLOT2_END_H], frame[IDX_HEAT_SLOT2_END_M])
            result["heat_slot2_enabled"] = bool(raw_s2 & MASK_SLOT_ENABLED)

        # Parse filter schedule from broadcast (bytes 29-36)
        if len(frame) > IDX_FILTER_SLOT2_END_M:
            raw_s1 = frame[IDX_FILTER_SLOT1_START_H]
            raw_s2 = frame[IDX_FILTER_SLOT2_START_H]
            result["filter_slot1_start"] = (raw_s1 & MASK_SLOT_HOUR, frame[IDX_FILTER_SLOT1_START_M])
            result["filter_slot1_end"] = (frame[IDX_FILTER_SLOT1_END_H], frame[IDX_FILTER_SLOT1_END_M])
            result["filter_slot1_enabled"] = bool(raw_s1 & MASK_SLOT_ENABLED)
            result["filter_slot2_start"] = (raw_s2 & MASK_SLOT_HOUR, frame[IDX_FILTER_SLOT2_START_M])
            result["filter_slot2_end"] = (frame[IDX_FILTER_SLOT2_END_H], frame[IDX_FILTER_SLOT2_END_M])
            result["filter_slot2_enabled"] = bool(raw_s2 & MASK_SLOT_ENABLED)

        return result

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return entity descriptions for P25B85."""
        return _P25B85_ENTITIES

    # ── Jets / pump helpers ───────────────────────────────────

    def get_jets_state(self, data: dict) -> str:
        """Return current jets state as 'off', 'low', or 'high'."""
        return data.get("jets", "off")

    # ── Dynamic command builders ──────────────────────────────
    # All commands use build_frame() to compute CRC dynamically.

    def _build_button_command(
        self,
        pump_b7: int = 0x00,
        pump_b8: int = 0x00,
        btn_group: int = 0x00,
        btn_action: int = 0x00,
        modifier: int = 0x00,
        context: int = 0xC0,
        setpoint_f: int = 0x62,
    ) -> bytes:
        """Build a type-0xA1 button command frame with CRC.

        Args:
            pump_b7/b8: pump transition bytes (non-zero for pump commands)
            btn_group: button group identifier
            btn_action: button action value
            modifier: modifier byte (0x80 for ozone mode)
            context: context byte (0xC0 normal, 0x40 ozone manual)
            setpoint_f: current setpoint in °F (embedded for panel compat)
        """
        from ..protocol import build_frame

        payload = bytearray([
            0x01, 0x20, 0x10, 0x3C, 0xA1, 0x10, 0xA1,
            pump_b7, pump_b8,
            btn_group, btn_action,
            modifier, context,
            0x00,
            setpoint_f,
            0x00,
        ])
        return build_frame(bytes(payload))

    def build_light_toggle_command(self) -> bytes:
        """Build a light toggle command."""
        return self._build_button_command(btn_group=0x40, btn_action=0x40)

    def build_pump_command(self, current: str, target: str) -> bytes | None:
        """Build a pump transition command.

        Returns None if no direct transition exists.
        """
        key = (current, target)
        if key not in _PUMP_TRANSITIONS:
            return None
        b7, b8 = _PUMP_TRANSITIONS[key]
        return self._build_button_command(pump_b7=b7, pump_b8=b8)

    def build_heater_command(self, on: bool) -> bytes:
        """Build a heater ON or OFF command."""
        return self._build_button_command(
            btn_group=0x08,
            btn_action=0x08 if on else 0x00,
        )

    def build_blower_command(self, on: bool) -> bytes:
        """Build a blower ON or OFF command.

        ON: btn_action=0x0C (0x04 device | 0x08 activate). Confirmed working.
        OFF: btn_action=0x00 (clear — matches heater OFF pattern).
        """
        return self._build_button_command(
            btn_group=0x04,
            btn_action=0x0C if on else 0x00,
        )

    def build_temp_command(self, target_celsius: int) -> bytes | None:
        """Build a temperature setpoint command frame with CRC.

        Converts °C to °F and builds the command dynamically.
        Returns None if out of range.

        btn_action=0x98 confirmed working via live test (0x80 failed).
        """
        if target_celsius < TEMP_MIN_C or target_celsius > TEMP_MAX_C:
            return None
        target_f = _celsius_to_fahrenheit(target_celsius)
        return self._build_button_command(
            btn_group=0x80,
            btn_action=0x98,
            setpoint_f=target_f,
        )

    def build_ozone_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone mode switch command (Auto or Manual).

        Args:
            mode: "auto" or "manual"
            setpoint_f: current setpoint in °F (controller ignores)
        """
        if mode == "auto":
            context = 0xC0
        elif mode == "manual":
            context = 0x40
        else:
            raise ValueError(f"Unsupported ozone mode: {mode}")

        return self._build_button_command(
            modifier=0x80,
            context=context,
            setpoint_f=setpoint_f,
        )

    def build_ozone_manual_command(self, on: bool, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone manual ON/OFF command.

        Requires ozone mode to be set to Manual first.
        """
        return self._build_button_command(
            btn_group=0x01,
            btn_action=0x01 if on else 0x10,
            context=0x40,
            setpoint_f=setpoint_f,
        )

    def build_schedule_command(
        self,
        schedule_type: str,
        slot1_start: tuple[int, int],
        slot1_end: tuple[int, int],
        slot2_start: tuple[int, int],
        slot2_end: tuple[int, int],
        slot1_enabled: bool = True,
        slot2_enabled: bool = True,
    ) -> bytes:
        """Build a schedule command frame with CRC.

        Args:
            schedule_type: "heat" or "filter"
            slot1_start: (hour, minute) for slot 1 start
            slot1_end: (hour, minute) for slot 1 end
            slot2_start: (hour, minute) for slot 2 start
            slot2_end: (hour, minute) for slot 2 end
            slot1_enabled: whether slot 1 is enabled
            slot2_enabled: whether slot 2 is enabled

        Returns:
            Wire-ready frame bytes.
        """
        from ..protocol import build_frame

        if schedule_type == "heat":
            cmd_type = 0xA3
        elif schedule_type == "filter":
            cmd_type = 0xA4
        else:
            raise ValueError(f"Unsupported schedule type: {schedule_type}")

        # Compute flags byte from enable state
        flags = SCHED_FLAGS_TABLE[(slot1_enabled, slot2_enabled)]

        # Command payload (16 bytes):
        # [0-6] header, [7] flags, [8-15] slot times
        payload = bytearray([
            0x01, 0x20, 0x10, 0x3C, cmd_type, 0x10, 0xA1,
            flags,
            slot1_start[0], slot1_start[1],  # slot 1 start h, m
            slot1_end[0], slot1_end[1],      # slot 1 end h, m
            slot2_start[0], slot2_start[1],  # slot 2 start h, m
            slot2_end[0], slot2_end[1],      # slot 2 end h, m
        ])
        return build_frame(bytes(payload))

    def build_datetime_command(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
        *,
        set_date: bool = True,
    ) -> bytes:
        """Build a DateTime set command frame with CRC.

        Args:
            year: Full year (e.g. 2026)
            month: 1-12
            day: 1-31
            hour: 0-23
            minute: 0-59
            second: 0-59
            set_date: If True (default), writes date AND time (prefix=0x05).
                If False, writes time only (prefix=0x50).

        Note:
            Captured from PB554 panel: prefix byte controls what is written.
            - 0x05 = date + time (panel uses this for date changes)
            - 0x50 = time only (panel uses this for time-only changes)

        Returns:
            Wire-ready frame bytes.
        """
        from ..protocol import build_frame

        prefix = 0x05 if set_date else 0x50
        payload = bytearray([
            0x01, 0x20, 0x10, 0x3C, 0xA2, 0x10, 0xA1,
            prefix,
            year - 2000,             # year offset
            month,
            day,
            hour,
            minute,
            second,
            0x00, 0x00,
        ])
        return build_frame(bytes(payload))


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
        name="Setpoint temperature",
        icon="mdi:thermometer-check",
        device_class="temperature",
        state_class="measurement",
        native_unit="°C",
    ),
    SpaEntityDescription(
        platform="sensor",
        key="status",
        name="Status",
        icon="mdi:waves",
        icon_map={
            "off": "mdi:waves",
            "circulation": "mdi:pump",
            "heating": "mdi:fire",
            "ozone": "mdi:shield-sun",
            "unknown": "mdi:help-circle-outline",
        },
        device_class="enum",
        options=["off", "circulation", "heating", "ozone", "unknown"],
    ),
    SpaEntityDescription(
        platform="sensor",
        key="jets",
        name="Jets",
        icon="mdi:weather-windy",
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
]
