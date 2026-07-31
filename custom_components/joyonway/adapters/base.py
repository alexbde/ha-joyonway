from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
from enum import StrEnum
import hashlib
from typing import Protocol

try:
    from homeassistant.util import dt as dt_util
except ImportError:
    dt_util = None  # type: ignore[assignment]


class JetType(StrEnum):
    """Jet speed capability."""

    SINGLE = "single"
    DUAL = "dual"


@dataclass(frozen=True)
class JetDescription:
    """Describes a jet/pump exposed by a model adapter."""

    id: str  # e.g. "jets", "jets_left", "jets_right"
    name: str
    type: JetType


@dataclass(frozen=True)
class SpaEntityDescription:
    """Describes an entity exposed by a model adapter."""

    platform: str  # "sensor" or "binary_sensor"
    key: str  # e.g. "current_temperature"
    name: str  # user-facing name
    icon: str | None = None
    icon_map: dict[str, str] | None = None  # state → icon for dynamic icons
    device_class: str | None = None
    state_class: str | None = None
    native_unit: str | None = None
    entity_category: str | None = None  # "diagnostic" or None
    enabled_by_default: bool = True
    options: list[str] | None = None  # for device_class="enum"
    format_hex: bool = False  # Set to True to format as 0xXX hex string


class ModelAdapter(Protocol):
    """Per-model byte mapping and feature support.

    Each controller model implements this to define:
    - How to identify its broadcast frames
    - How to parse status from a logical (unescaped) frame
    - Which entities to expose in Home Assistant
    """

    model: str
    broadcast_signature: bytes
    unescape_full_frame: bool
    supports_writes: bool
    jets: list[JetDescription]
    supported_light_colors: list[str]
    has_blower: bool
    temp_min_c: int
    temp_max_c: int
    supports_mode_switching: bool

    def color_index_to_name(self, index: int) -> str | None:
        """Map color index to name."""
        ...

    def color_name_to_index(self, name: str) -> int | None:
        """Map color name to index."""
        ...

    def matches_signature(self, frame: bytes) -> bool:
        """Return True if the unescaped frame header matches this model.

        Header-only check (no length or CRC requirement) so that model
        detection and runtime parsing always agree on the same criteria.
        """
        ...

    def unsupported_board_version(self, frame: bytes) -> int | None:
        """Return byte 7 if this is our model but its board version is unknown."""
        ...

    def parse_status(self, frame: bytes) -> dict | None:
        """Extract state dict from an unescaped broadcast frame.

        Returns None if the frame doesn't match this model's signature.
        """
        ...

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        """Return the list of entities this model exposes."""
        ...

    def is_heater_enabled(self, data: dict | None) -> bool | None:
        """Derive heater enabled state from status if not explicitly present."""
        ...

    def get_jets_state(self, data: dict, jet_id: str) -> str:
        """Return current jets state as 'off', 'low', or 'high'."""
        ...

    def build_light_command(self, on: bool, color: str | None = None) -> bytes:
        """Build a light ON or OFF command.

        For controllers supporting discrete color presets, this builds the
        command corresponding to the requested color name.
        """
        ...

    def build_jets_command(self, jet_id: str, target: str) -> bytes | None:
        """Build a jets command for the desired target state."""
        ...

    def build_heater_command(self, on: bool) -> bytes:
        """Build a heater ON or OFF command."""
        ...

    def build_blower_command(self, on: bool) -> bytes:
        """Build a blower ON or OFF command."""
        ...

    def build_temp_command(self, target_celsius: int) -> bytes | None:
        """Build a temperature setpoint command."""
        ...

    def build_ozone_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone mode switch command."""
        ...

    def build_heater_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        """Build a heater mode switch command."""
        ...

    def build_ozone_manual_command(self, on: bool, setpoint_f: int = 0x62) -> bytes:
        """Build an ozone manual ON/OFF command."""
        ...

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
        """Build a schedule command frame."""
        ...

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
        """Build a DateTime set command."""
        ...

    def build_time_command(
        self,
        hour: int,
        minute: int,
        second: int,
        year: int = 2000,
        month: int = 1,
        day: int = 1,
    ) -> bytes:
        """Build a Time-only set command."""
        ...

    def build_date_command(
        self,
        year: int,
        month: int,
        day: int,
        hour: int,
        minute: int,
        second: int,
    ) -> bytes:
        """Build a Date-only / Date & Time set command."""
        ...


# Logical indices and masks for RS485 payload parsing
# Number of header bytes required before a model signature can be evaluated.
MIN_SIGNATURE_LENGTH = 9
# Shortest unescaped broadcast frame that carries a full status payload.
MIN_BROADCAST_FRAME_LENGTH = 30

# Broadcast header: 1A FF 01 3C D2 B4 FF <board_version> <family>
# Byte 7 is the controller board version shown on the touchpad under
# Settings -> About (minor digit only: 0x08 = v1.8, 0x06 = v1.6).
IDX_BOARD_VERSION = 7
IDX_MODEL_FAMILY = 8
# Board versions whose payload layout is confirmed on real hardware.
KNOWN_BOARD_VERSIONS: tuple[int, ...] = (0x06, 0x08)


def format_board_version(value: int) -> str:
    """Render byte 7 the way the touchpad displays it."""
    return f"1.{value:d}"


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
MASK_BLOWER_CONFIG = 0x08

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

MASK_HEATING_CYCLE = 0x80
MASK_ACTIVITY = 0x20
MASK_HEATER_BLOWER = 0x08

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

_MAPPED_INDEXES = {
    *range(9),  # header bytes 0-8
    IDX_CURRENT_TEMP,
    IDX_JET_BYTE,
    IDX_OZONE_MODE,
    IDX_HEATER_STATE,
    IDX_SETPOINT,
    IDX_LIGHT_CYCLE,
    IDX_ACTIVITY_FLAG,
    *range(IDX_HEAT_SLOT1_START_H, IDX_HEAT_SLOT2_END_M + 1),
    *range(IDX_FILTER_SLOT1_START_H, IDX_FILTER_SLOT2_END_M + 1),
    *range(IDX_DATETIME_START, IDX_DATETIME_START + 6),
}


def fahrenheit_to_celsius(f: int) -> int | None:
    """Convert Fahrenheit to Celsius, return None for invalid values."""
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9)


def celsius_to_fahrenheit(c: int) -> int:
    """Convert Celsius to Fahrenheit (integer, standard rounding)."""
    return round(c * 9 / 5 + 32)


class JoyonwayBaseAdapter:
    """Shared base class for Joyonway model adapters."""

    model: str
    broadcast_signature: bytes
    unescape_full_frame: bool = True
    supports_writes: bool = True
    jets: list[JetDescription]
    supported_light_colors: list[str] = []
    has_blower: bool = False
    temp_min_c: int = 10
    temp_max_c: int = 40
    supports_mode_switching: bool = True

    heater_state_map: dict[int, str]
    _cmd_prefix_byte: int
    _cmd_context_flag: int
    _mask_light: int = 0x0F  # override in P23BaseAdapter to 0x01

    def color_index_to_name(self, index: int) -> str | None:
        """Map color index to name (default implementation returns None)."""
        return None

    def color_name_to_index(self, name: str) -> int | None:
        """Map color name to index (default implementation returns None)."""
        return None

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
        return data.get(jet_id, "off")

    def _check_signature(self, frame: bytes) -> bool:
        """Check if frame matches adapter broadcast signature."""
        signature = self.broadcast_signature
        return (
            frame[:IDX_BOARD_VERSION] == signature[:IDX_BOARD_VERSION]
            and frame[IDX_BOARD_VERSION] in KNOWN_BOARD_VERSIONS
            and frame[IDX_MODEL_FAMILY] == signature[IDX_MODEL_FAMILY]
        )

    def unsupported_board_version(self, frame: bytes) -> int | None:
        """Return byte 7 if this is our model but its board version is unknown.

        Distinguishes "supported model, untested board version" from a frame
        that belongs to a different model family entirely.
        """
        if len(frame) < MIN_SIGNATURE_LENGTH:
            return None
        signature = self.broadcast_signature
        if (
            frame[:IDX_BOARD_VERSION] != signature[:IDX_BOARD_VERSION]
            or frame[IDX_MODEL_FAMILY] != signature[IDX_MODEL_FAMILY]
        ):
            return None
        version = frame[IDX_BOARD_VERSION]
        return None if version in KNOWN_BOARD_VERSIONS else version

    def matches_signature(self, frame: bytes) -> bool:
        """Return True if the unescaped frame header matches this model.

        Public, length-safe wrapper around ``_check_signature``. Model
        detection (config flow) and runtime parsing both go through this so
        they can never disagree about which frames belong to a model.
        """
        if len(frame) < MIN_SIGNATURE_LENGTH:
            return False
        return self._check_signature(frame)

    def _post_parse_status(
        self,
        result: dict,
        frame: bytes,
        jet_byte: int,
        ozone_mode_byte: int,
        heater_byte: int,
    ) -> None:
        """hook for subclass to customize/post-process status dict."""
        pass

    def parse_status(self, frame: bytes) -> dict | None:
        """Extract state dict from an unescaped broadcast frame."""
        if len(frame) < MIN_BROADCAST_FRAME_LENGTH:
            return None
        if not self.matches_signature(frame):
            return None

        current_temp_f = frame[IDX_CURRENT_TEMP]
        setpoint_f = frame[IDX_SETPOINT]
        jet_byte = frame[IDX_JET_BYTE]
        ozone_mode_byte = frame[IDX_OZONE_MODE]
        heater_byte = frame[IDX_HEATER_STATE]
        light_byte = frame[IDX_LIGHT_CYCLE]
        activity_byte = frame[IDX_ACTIVITY_FLAG]

        heater_base = heater_byte & ~MASK_HEATER_BLOWER
        status = self.heater_state_map.get(heater_base, "unknown")

        heating_cycle_active = bool(light_byte & MASK_HEATING_CYCLE)
        if status in ("off", "standby") and heating_cycle_active:
            status = "circulation"

        result: dict = {
            "current_temperature": fahrenheit_to_celsius(current_temp_f),
            "setpoint": fahrenheit_to_celsius(setpoint_f),
            "light": bool(light_byte & self._mask_light),
            "light_color_index": light_byte & self._mask_light,
            "heater_active": self.heater_state_map.get(heater_base) == "heating",
            "heater_enabled": status in ("standby", "circulation", "heating"),
            "status": status,
            "heater_byte": heater_byte,
            "ozone_active": self.heater_state_map.get(heater_base) == "ozone",
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

        self._post_parse_status(result, frame, jet_byte, ozone_mode_byte, heater_byte)
        return result

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
        """Build a schedule command frame."""
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
                self._cmd_prefix_byte,
                0x10,
                0x3C,
                cmd_type,
                self._cmd_context_flag,
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
        """Build a DateTime set command."""
        from ..protocol import build_frame

        prefix = 0x05 if set_date else 0x50
        payload = bytearray(
            [
                0x01,
                self._cmd_prefix_byte,
                0x10,
                0x3C,
                0xA2,
                self._cmd_context_flag,
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
        """Build a Time-only set command."""
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
        """Build a Date-only / Date & Time set command."""
        return self.build_datetime_command(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            set_date=True,
        )
