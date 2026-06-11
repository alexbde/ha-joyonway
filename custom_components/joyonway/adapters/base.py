"""Base model adapter interface for Joyonway spa controllers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


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

    def build_light_command(self, on: bool) -> bytes:
        """Build a light ON or OFF command.

        For toggle-only controllers (P25B85), this builds a toggle frame
        regardless of the `on` value — the entity layer handles no-op detection.
        For discrete-command controllers (P23B32), this builds the appropriate
        ON or OFF frame.
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
