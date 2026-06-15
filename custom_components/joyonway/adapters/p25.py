"""P25 model family adapter — byte map and entity definitions.

Byte positions validated against local RS485 captures.
All indexes are 0-based logical-frame positions (after full-frame unescape).
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from typing import ClassVar

try:
    from homeassistant.util import dt as dt_util
except ImportError:  # standalone / test usage without HA
    dt_util = None  # type: ignore[assignment]

from .base import JetDescription, JetType, SpaEntityDescription

# Broadcast frame header signature for P25 (bytes 0-8)
# Both P25B85 and P25B37 broadcast 0x03 at index 8
P25_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])

# Byte positions in the logical (unescaped) broadcast frame (0-based)
IDX_CURRENT_TEMP = 9  # Fahrenheit
IDX_JET_BYTE = 12  # 0x02=low, 0x04=high
IDX_OZONE_MODE = 13  # bit 7: 0=Auto, 1=Manual
IDX_HEATER_STATE = 14
IDX_SETPOINT = 16  # Fahrenheit
IDX_LIGHT_CYCLE = 17
IDX_ACTIVITY_FLAG = 28

# Ozone mode mask (byte 13)
MASK_OZONE_MODE_MANUAL = 0x80  # bit 7 set = Manual mode
MASK_HEATER_MODE_MANUAL = 0x10  # bit 4 set = Manual mode
IDX_DATETIME_START = 53  # bytes 53-58: year, month, day, hour, minute, second

# Schedule byte positions in broadcast frame
# Layout per schedule: [s1_start_h] [s1_start_m] [s1_end_h] [s1_end_m]
#                      [s2_start_h] [s2_start_m] [s2_end_h] [s2_end_m]
# Start-hour bytes encode: hour | 0x40 when slot is enabled, plain hour when disabled
MASK_SLOT_ENABLED = 0x40  # bit 6 on start-hour byte = slot enabled
MASK_SLOT_HOUR = 0x3F  # lower 6 bits = hour value (0-23)

# Heat schedule: broadcast bytes 19-26
IDX_HEAT_SLOT1_START_H = 19
IDX_HEAT_SLOT1_START_M = 20
IDX_HEAT_SLOT1_END_H = 21
IDX_HEAT_SLOT1_END_M = 22
IDX_HEAT_SLOT2_START_H = 23
IDX_HEAT_SLOT2_START_M = 24
IDX_HEAT_SLOT2_END_H = 25
IDX_HEAT_SLOT2_END_M = 26

# Filter schedule: broadcast bytes 29-36
IDX_FILTER_SLOT1_START_H = 29
IDX_FILTER_SLOT1_START_M = 30
IDX_FILTER_SLOT1_END_H = 31
IDX_FILTER_SLOT1_END_M = 32
IDX_FILTER_SLOT2_START_H = 33
IDX_FILTER_SLOT2_START_M = 34
IDX_FILTER_SLOT2_END_H = 35
IDX_FILTER_SLOT2_END_M = 36

# Schedule flags for pure enable-state commands.
SCHED_FLAGS_STATE_TABLE: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x62,
    (False, True): 0x9A,
    (False, False): 0x52,
}

# Schedule flags for TIME writes.
SCHED_FLAGS_TIME_WRITE_TABLE: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x6A,
    (False, True): 0x9A,
    (False, False): 0x5A,
}

# Jet masks
MASK_JET_LOW = 0x02
MASK_JET_HIGH = 0x04

# Light
MASK_LIGHT = 0x01

# Heating cycle active flag at byte 17 (bit 7).
MASK_HEATING_CYCLE = 0x80

# Activity flag at byte 28
MASK_ACTIVITY = 0x20

# Heater state values (at byte 14)
MASK_HEATER_BLOWER = 0x08  # bit 3 on heater byte = blower running

_TRAILER_LEN = 5  # CRC32 (4) + frame end delimiter (1)
HEATER_OFF = 0x40
HEATER_STANDBY = 0x50
HEATER_CIRCULATION = 0x51
HEATER_HEATING = 0x55
HEATER_HEATING_ALT = 0x54
HEATER_OZONE = 0x41
HEATER_OZONE_ALT = 0xC1

HEATER_STATE_MAP: dict[int, str] = {
    HEATER_OFF: "off",
    HEATER_STANDBY: "standby",
    HEATER_CIRCULATION: "circulation",
    HEATER_HEATING: "heating",
    HEATER_HEATING_ALT: "heating",
    HEATER_OZONE: "ozone",
    HEATER_OZONE_ALT: "ozone",
}

_MAPPED_INDEXES = {
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    12,
    13,
    14,
    16,
    17,
    28,
    19,
    20,
    21,
    22,
    23,
    24,
    25,
    26,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    53,
    54,
    55,
    56,
    57,
    58,
}

_JET_TARGET_BYTES: dict[str, tuple[int, int]] = {
    "off": (0x04, 0x00),
    "low": (0x02, 0x02),
    "high": (0x06, 0x04),
}

TEMP_MIN_C = 10
TEMP_MAX_C = 40


def _fahrenheit_to_celsius(f: int) -> int | None:
    """Convert Fahrenheit to Celsius, return None for invalid values."""
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9)


def _celsius_to_fahrenheit(c: int) -> int:
    """Convert Celsius to Fahrenheit (integer, standard rounding)."""
    return round(c * 9 / 5 + 32)


class P25BaseAdapter:
    """Base adapter for the Joyonway P25 model family."""

    model: str
    broadcast_signature: bytes = P25_SIGNATURE
    unescape_full_frame: bool = True
    supports_writes: bool = True
    jets: list[JetDescription] = [
        JetDescription(id="jets", name="Jets", type=JetType.DUAL),
    ]

    _context_byte: ClassVar[int]

    def parse_status(self, frame: bytes) -> dict | None:
        """Extract state dict from an unescaped broadcast frame.

        Returns None if frame doesn't match P25 signature or is too short.
        """
        if len(frame) < 30:
            return None
        # Check signature (first 9 bytes)
        if frame[: len(self.broadcast_signature)] != self.broadcast_signature:
            return None

        current_temp_f = frame[IDX_CURRENT_TEMP]
        setpoint_f = frame[IDX_SETPOINT]
        jet_byte = frame[IDX_JET_BYTE]
        ozone_mode_byte = frame[IDX_OZONE_MODE]
        heater_byte = frame[IDX_HEATER_STATE]
        light_byte = frame[IDX_LIGHT_CYCLE]
        activity_byte = frame[IDX_ACTIVITY_FLAG]

        heater_base = heater_byte & ~MASK_HEATER_BLOWER
        status = HEATER_STATE_MAP.get(heater_base, "unknown")

        heating_cycle_active = bool(light_byte & MASK_HEATING_CYCLE)
        if status in ("off", "standby") and heating_cycle_active:
            status = "circulation"

        if jet_byte & MASK_JET_HIGH:
            jets = "high"
        elif jet_byte & MASK_JET_LOW:
            jets = "low"
        else:
            jets = "off"

        ozone_mode_manual = bool(ozone_mode_byte & MASK_OZONE_MODE_MANUAL)
        heater_mode_manual = bool(ozone_mode_byte & MASK_HEATER_MODE_MANUAL)

        result: dict = {
            "current_temperature": _fahrenheit_to_celsius(current_temp_f),
            "setpoint": _fahrenheit_to_celsius(setpoint_f),
            "jet_low": bool(jet_byte & MASK_JET_LOW),
            "jet_high": bool(jet_byte & MASK_JET_HIGH),
            "jets": jets,
            "light": bool(light_byte & MASK_LIGHT),
            "heater_active": heater_base in (HEATER_HEATING, HEATER_HEATING_ALT),
            "heater_enabled": bool(heater_byte & 0x10),
            "status": status,
            "heater_byte": heater_byte,
            "ozone_active": heater_base in (HEATER_OZONE, HEATER_OZONE_ALT),
            "ozone_mode": "manual" if ozone_mode_manual else "auto",
            "heater_mode": "manual" if heater_mode_manual else "auto",
            "blower": bool(heater_byte & MASK_HEATER_BLOWER),
            "heater_byte_raw": heater_byte,
            "jets_byte_raw": jet_byte,
            "ozone_mode_byte_raw": ozone_mode_byte,
            "activity_byte_raw": activity_byte,
            "light_cycle_byte_raw": light_byte,
            "frame_length": len(frame),
        }

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

        if len(frame) > IDX_HEAT_SLOT2_END_M:
            raw_s1 = frame[IDX_HEAT_SLOT1_START_H]
            raw_s2 = frame[IDX_HEAT_SLOT2_START_H]
            result["heat_slot1_start"] = (
                raw_s1 & MASK_SLOT_HOUR,
                frame[IDX_HEAT_SLOT1_START_M],
            )
            result["heat_slot1_end"] = (
                frame[IDX_HEAT_SLOT1_END_H],
                frame[IDX_HEAT_SLOT1_END_M],
            )
            result["heat_slot1_enabled"] = bool(raw_s1 & MASK_SLOT_ENABLED)
            result["heat_slot2_start"] = (
                raw_s2 & MASK_SLOT_HOUR,
                frame[IDX_HEAT_SLOT2_START_M],
            )
            result["heat_slot2_end"] = (
                frame[IDX_HEAT_SLOT2_END_H],
                frame[IDX_HEAT_SLOT2_END_M],
            )
            result["heat_slot2_enabled"] = bool(raw_s2 & MASK_SLOT_ENABLED)

        if len(frame) > IDX_FILTER_SLOT2_END_M:
            raw_s1 = frame[IDX_FILTER_SLOT1_START_H]
            raw_s2 = frame[IDX_FILTER_SLOT2_START_H]
            result["filter_slot1_start"] = (
                raw_s1 & MASK_SLOT_HOUR,
                frame[IDX_FILTER_SLOT1_START_M],
            )
            result["filter_slot1_end"] = (
                frame[IDX_FILTER_SLOT1_END_H],
                frame[IDX_FILTER_SLOT1_END_M],
            )
            result["filter_slot1_enabled"] = bool(raw_s1 & MASK_SLOT_ENABLED)
            result["filter_slot2_start"] = (
                raw_s2 & MASK_SLOT_HOUR,
                frame[IDX_FILTER_SLOT2_START_M],
            )
            result["filter_slot2_end"] = (
                frame[IDX_FILTER_SLOT2_END_H],
                frame[IDX_FILTER_SLOT2_END_M],
            )
            result["filter_slot2_enabled"] = bool(raw_s2 & MASK_SLOT_ENABLED)

        payload_end = max(0, len(frame) - _TRAILER_LEN)
        digest_input = bytearray()
        for i in range(payload_end):
            if i in _MAPPED_INDEXES:
                continue
            digest_input.extend((i & 0xFF, frame[i]))

        result["unmapped_bytes_hash"] = hashlib.md5(
            bytes(digest_input), usedforsecurity=False
        ).hexdigest()[:8]

        return result

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return entity descriptions for P25."""
        return _P25_ENTITIES

    def is_heater_enabled(self, data: dict | None) -> bool | None:
        """Derive heater enabled state from status if not explicitly present."""
        if data is None:
            return None
        val = data.get("heater_enabled")
        if val is None:
            status = data.get("status")
            if status is not None:
                val = status in ("standby", "circulation", "heating")
        return val

    def get_jets_state(self, data: dict, jet_id: str) -> str:
        """Return current jets state as 'off', 'low', or 'high'."""
        if jet_id == "jets":
            return data.get("jets", "off")
        return "off"

    def _build_button_command(
        self,
        jet_b7: int = 0x00,
        jet_b8: int = 0x00,
        btn_group: int = 0x00,
        btn_action: int = 0x00,
        modifier: int = 0x00,
        context: int | None = None,
        setpoint_f: int = 0x62,
        tail_byte: int = 0x00,
    ) -> bytes:
        """Build a type-0xA1 button command frame with CRC."""
        from ..protocol import build_frame

        if context is None:
            context = self._context_byte

        payload = bytearray(
            [
                0x01,
                0x20,
                0x10,
                0x3C,
                0xA1,
                0x10,
                0xA1,
                jet_b7,
                jet_b8,
                btn_group,
                btn_action,
                modifier,
                context,
                0x00,
                setpoint_f,
                tail_byte,
            ]
        )
        return build_frame(bytes(payload))

    def build_light_command(self, on: bool) -> bytes:
        """Build a light command."""
        raise NotImplementedError

    def build_jets_command(self, jet_id: str, target: str) -> bytes | None:
        """Build a jets command for the desired target state."""
        if jet_id != "jets" or target not in _JET_TARGET_BYTES:
            return None
        b7, b8 = _JET_TARGET_BYTES[target]
        return self._build_button_command(jet_b7=b7, jet_b8=b8)

    def build_heater_command(self, on: bool) -> bytes:
        """Build a heater ON or OFF command."""
        return self._build_button_command(
            btn_group=0x08,
            btn_action=0x08 if on else 0x00,
        )

    def build_blower_command(self, on: bool) -> bytes:
        """Build a blower ON or OFF command."""
        return self._build_button_command(
            btn_group=0x04,
            btn_action=0x0C if on else 0x00,
        )

    def build_temp_command(self, target_celsius: int) -> bytes | None:
        """Build a temperature setpoint command frame with CRC."""
        if target_celsius < TEMP_MIN_C or target_celsius > TEMP_MAX_C:
            return None
        target_f = _celsius_to_fahrenheit(target_celsius)
        return self._build_button_command(
            btn_group=0x80,
            btn_action=0x98,
            setpoint_f=target_f,
        )

    def build_ozone_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone mode switch command (Auto or Manual)."""
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

    def build_heater_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        """Build a heater mode switch command (Auto or Manual)."""
        if mode == "auto":
            context = 0x80
        elif mode == "manual":
            context = 0xC0
        else:
            raise ValueError(f"Unsupported heater mode: {mode}")

        return self._build_button_command(
            modifier=0x40,
            context=context,
            setpoint_f=setpoint_f,
        )

    def build_ozone_manual_command(self, on: bool, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone manual ON/OFF command."""
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
        *,
        write_mode: str = "state",
    ) -> bytes:
        """Build a schedule command frame with CRC."""
        from ..protocol import build_frame

        cmd_type = {"heat": 0xA3, "filter": 0xA4}.get(schedule_type)
        if cmd_type is None:
            raise ValueError(f"Unsupported schedule type: {schedule_type}")

        if write_mode == "state":
            table = SCHED_FLAGS_STATE_TABLE
        elif write_mode == "time":
            table = SCHED_FLAGS_TIME_WRITE_TABLE
        else:
            raise ValueError(f"Unsupported schedule write mode: {write_mode}")

        flags = table[(slot1_enabled, slot2_enabled)]

        payload = bytearray(
            [
                0x01,
                0x20,
                0x10,
                0x3C,
                cmd_type,
                0x10,
                0xA1,
                flags,
                slot1_start[0],
                slot1_start[1],
                slot1_end[0],
                slot1_end[1],
                slot2_start[0],
                slot2_start[1],
                slot2_end[0],
                slot2_end[1],
            ]
        )
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
        """Build a DateTime set command frame with CRC."""
        from ..protocol import build_frame

        prefix = 0x05 if set_date else 0x50
        payload = bytearray(
            [
                0x01,
                0x20,
                0x10,
                0x3C,
                0xA2,
                0x10,
                0xA1,
                prefix,
                year - 2000,
                month,
                day,
                hour,
                minute,
                second,
                0x00,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_time_command(
        self,
        hour: int,
        minute: int,
        second: int,
        year: int = 2000,
        month: int = 1,
        day: int = 1,
    ) -> bytes:
        """Build a Time-only set command frame (prefix 0x50)."""
        return self.build_datetime_command(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            set_date=False,
        )

    def build_date_command(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
    ) -> bytes:
        """Build a Date-only / Date & Time set command frame (prefix 0x05)."""
        return self.build_datetime_command(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            set_date=True,
        )


class P25B85Adapter(P25BaseAdapter):
    """Adapter for the Joyonway P25B85 controller."""

    model = "P25B85"
    _context_byte = 0xC0

    def build_light_command(self, on: bool) -> bytes:
        """P25B85 uses a toggle command; `on` is ignored."""
        return self._build_button_command(btn_group=0x40, btn_action=0x40)


class P25B37Adapter(P25BaseAdapter):
    """Adapter for the Joyonway P25B37 controller."""

    model = "P25B37"
    _context_byte = 0x40

    def build_light_command(self, on: bool) -> bytes:
        """P25B37 uses discrete ON/OFF via payload byte 15."""
        return self._build_button_command(
            btn_group=0x40,
            btn_action=0x40,
            tail_byte=0x81 if on else 0x80,
        )


_P25_ENTITIES: list[SpaEntityDescription] = [
    # Sensors
    SpaEntityDescription(
        platform="sensor",
        key="current_temperature",
        name="Current temperature",
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
            "standby": "mdi:timer-sand",
            "circulation": "mdi:pump",
            "heating": "mdi:fire",
            "ozone": "mdi:shield-sun",
            "unknown": "mdi:help-circle-outline",
        },
        device_class="enum",
        options=["off", "standby", "circulation", "heating", "ozone", "unknown"],
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
    SpaEntityDescription(
        platform="sensor",
        key="heater_byte_raw",
        name="Heater byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="jets_byte_raw",
        name="Jets byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="ozone_mode_byte_raw",
        name="Ozone mode byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="activity_byte_raw",
        name="Activity byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="light_cycle_byte_raw",
        name="Light/cycle byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="frame_length",
        name="Frame length",
        icon="mdi:ruler",
        state_class="measurement",
        native_unit="bytes",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="unmapped_bytes_hash",
        name="Unmapped bytes hash",
        icon="mdi:fingerprint",
        entity_category="diagnostic",
        enabled_by_default=False,
    ),
]
