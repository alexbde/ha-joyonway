"""P23B32 / P20B29 model adapter — byte map and entity definitions.

Protocol differences from P25B85:
- Unescape policy: Tail-only (full payload unescape corrupts data).
- Broadcast signature byte: 0x02.
- Panel prefix for commands: 0x30 instead of 0x20.
- Independent single-speed pumps instead of one dual-speed pump.
- Distinct discrete ON/OFF commands rather than cycle states.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

try:
    from homeassistant.util import dt as dt_util
except ImportError:
    dt_util = None  # type: ignore[assignment]

from .base import JetDescription, JetType, SpaEntityDescription

P23B32_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])

IDX_CURRENT_TEMP = 9
IDX_JET_BYTE = 12
IDX_OZONE_MODE = 13
IDX_HEATER_STATE = 14
IDX_SETPOINT = 16
IDX_LIGHT_CYCLE = 17
IDX_ACTIVITY_FLAG = 28
IDX_DATETIME_START = 53

MASK_OZONE_MODE_MANUAL = 0x80
MASK_HEATER_MODE_MANUAL = 0x10

IDX_HEAT_SLOT1_START_H = 19
IDX_HEAT_SLOT1_START_M = 20
IDX_HEAT_SLOT1_END_H = 21
IDX_HEAT_SLOT1_END_M = 22
IDX_HEAT_SLOT2_START_H = 23
IDX_HEAT_SLOT2_START_M = 24
IDX_HEAT_SLOT2_END_H = 25
IDX_HEAT_SLOT2_END_M = 26

IDX_FILTER_SLOT1_START_H = 29
IDX_FILTER_SLOT1_START_M = 30
IDX_FILTER_SLOT1_END_H = 31
IDX_FILTER_SLOT1_END_M = 32
IDX_FILTER_SLOT2_START_H = 33
IDX_FILTER_SLOT2_START_M = 34
IDX_FILTER_SLOT2_END_H = 35
IDX_FILTER_SLOT2_END_M = 36

MASK_SLOT_ENABLED = 0x40
MASK_SLOT_HOUR = 0x3F

SCHED_FLAGS_STATE_TABLE: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x62,
    (False, True): 0x9A,
    (False, False): 0x52,
}

SCHED_FLAGS_TIME_WRITE_TABLE: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x6A,
    (False, True): 0x9A,
    (False, False): 0x5A,
}

MASK_JET_LEFT = 0x04
MASK_JET_RIGHT = 0x10
MASK_LIGHT = 0x01
MASK_HEATING_CYCLE = 0x80
MASK_ACTIVITY = 0x20
MASK_HEATER_BLOWER = 0x08

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

TEMP_MIN_C = 10
TEMP_MAX_C = 40


def _fahrenheit_to_celsius(f: int) -> int | None:
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9)


def _celsius_to_fahrenheit(c: int) -> int:
    return round(c * 9 / 5 + 32)


class P23B32Adapter:
    """Adapter for the Joyonway P23B32 controller."""

    model: str = "P23B32"
    broadcast_signature: bytes = P23B32_SIGNATURE
    unescape_full_frame: bool = False
    supports_writes: bool = True
    jets: list[JetDescription] = [
        JetDescription(id="jets_left", name="Jets Left", type=JetType.SINGLE),
        JetDescription(id="jets_right", name="Jets Right", type=JetType.SINGLE),
    ]

    def parse_status(self, frame: bytes) -> dict | None:
        if len(frame) < 30:
            return None
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

        ozone_mode_manual = bool(ozone_mode_byte & MASK_OZONE_MODE_MANUAL)
        heater_mode_manual = bool(ozone_mode_byte & MASK_HEATER_MODE_MANUAL)

        result: dict = {
            "current_temperature": _fahrenheit_to_celsius(current_temp_f),
            "setpoint": _fahrenheit_to_celsius(setpoint_f),
            "jets_left": "on" if (jet_byte & MASK_JET_LEFT) else "off",
            "jets_right": "on" if (jet_byte & MASK_JET_RIGHT) else "off",
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

        payload_end = max(0, len(frame) - 5)
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
        return _P23B32_ENTITIES

    def is_heater_enabled(self, data: dict | None) -> bool | None:
        if data is None:
            return None
        val = data.get("heater_enabled")
        if val is None:
            status = data.get("status")
            if status is not None:
                val = status in ("standby", "circulation", "heating")
        return val

    def get_jets_state(self, data: dict, jet_id: str) -> str:
        if jet_id == "jets_left":
            return data.get("jets_left", "off")
        elif jet_id == "jets_right":
            return data.get("jets_right", "off")
        return "off"

    def build_light_command(self, on: bool) -> bytes:
        """Build a discrete light ON or OFF command for P23B32."""
        from ..protocol import build_frame

        last_byte = 0x81 if on else 0x80
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                0x00,
                0x00,
                0x00,
                0x40,
                0x40,
                0x02,
                0x04,
                0x00,
                0x00,
                last_byte,
            ]
        )
        return build_frame(bytes(payload))

    def build_jets_command(self, jet_id: str, target: str) -> bytes | None:
        from ..protocol import build_frame

        is_on = target in ("low", "high", "on")  # single speed treats low/high/on as ON

        if jet_id == "jets_left":
            # ON: 01 30 10 3C A1 00 A1 06 04 00 00 02 04 00 00 00
            # OFF: 01 30 10 3C A1 00 A1 06 00 00 00 02 04 00 00 00
            if is_on:
                b7, b8 = 0x06, 0x04
            else:
                b7, b8 = 0x06, 0x00
        elif jet_id == "jets_right":
            # ON: 01 30 10 3C A1 00 A1 18 10 00 00 02 04 00 00 00
            # OFF: 01 30 10 3C A1 00 A1 18 00 00 00 02 04 00 00 00
            if is_on:
                b7, b8 = 0x18, 0x10
            else:
                b7, b8 = 0x18, 0x00
        else:
            return None

        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                b7,
                b8,
                0x00,
                0x00,
                0x02,
                0x04,
                0x00,
                0x00,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_heater_command(self, on: bool) -> bytes:
        from ..protocol import build_frame

        # Expected ON: 01 30 10 3C A1 00 A1 00 00 08 18 02 04 00 00 00
        # Expected OFF: 01 30 10 3C A1 00 A1 00 00 08 11 02 04 00 00 00
        b10 = 0x18 if on else 0x11
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                0x00,
                0x00,
                0x08,
                b10,
                0x02,
                0x04,
                0x00,
                0x00,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_blower_command(self, on: bool) -> bytes:
        from ..protocol import build_frame

        # ON: 01 30 10 3C A1 00 A1 00 00 04 04 02 04 00 00 00
        # OFF: 01 30 10 3C A1 00 A1 00 00 04 00 02 04 00 00 00
        b10 = 0x04 if on else 0x00
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                0x00,
                0x00,
                0x04,
                b10,
                0x02,
                0x04,
                0x00,
                0x00,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_temp_command(self, target_celsius: int) -> bytes | None:
        from ..protocol import build_frame

        if target_celsius < TEMP_MIN_C or target_celsius > TEMP_MAX_C:
            return None
        target_f = _celsius_to_fahrenheit(target_celsius)
        # Direct Set: 01 30 10 3C A1 00 A1 00 00 80 80 02 04 00 [temp_f] 00
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                0x00,
                0x00,
                0x80,
                0x80,
                0x02,
                0x04,
                0x00,
                target_f,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_ozone_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        # Fallback to empty command for now since protocol.md doesn't document
        # the exact frame for P23 config mode switch.
        return b""

    def build_heater_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        # Fallback to empty command
        return b""

    def build_ozone_manual_command(self, on: bool, setpoint_f: int = 0x62) -> bytes:
        from ..protocol import build_frame

        # Expected ON/OFF (16-byte):
        # ON: 01 30 10 3C A1 00 A1 00 00 01 01 02 04 00 00 00
        # OFF: 01 30 10 3C A1 00 A1 00 00 01 10 02 04 00 00 00
        b10 = 0x01 if on else 0x10
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                0x00,
                0x00,
                0x01,
                b10,
                0x02,
                0x04,
                0x00,
                0x00,
                0x00,
            ]
        )
        return build_frame(bytes(payload))

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
                0x30,
                0x10,
                0x3C,
                cmd_type,
                0x00,
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
        from ..protocol import build_frame

        prefix = 0x05 if set_date else 0x50
        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA2,
                0x00,
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
        return self.build_datetime_command(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            set_date=True,
        )


_P23B32_ENTITIES: list[SpaEntityDescription] = [
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
        key="jets_left",
        name="Jets Left",
        icon="mdi:weather-windy",
        device_class="enum",
        options=["off", "on"],
    ),
    SpaEntityDescription(
        platform="sensor",
        key="jets_right",
        name="Jets Right",
        icon="mdi:weather-windy",
        device_class="enum",
        options=["off", "on"],
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
