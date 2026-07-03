"""P23B32 / P20B29 model adapter — byte map and entity definitions.

Protocol differences from P25B85:
- Unescape policy: Tail-only (full payload unescape corrupts data).
- Broadcast signature byte: 0x02.
- Panel prefix for commands: 0x30 instead of 0x20.
- Independent single-speed pumps instead of one dual-speed pump.
- Distinct discrete ON/OFF commands rather than cycle states.
"""

from __future__ import annotations

from typing import ClassVar

from .base import (
    JoyonwayBaseAdapter,
    JetDescription,
    JetType,
    SpaEntityDescription,
    celsius_to_fahrenheit,
    MASK_OZONE_MODE_MANUAL,
    MASK_HEATER_MODE_MANUAL,
)

P23B32_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])

MASK_JET_LEFT = 0x04
MASK_JET_RIGHT = 0x10
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


class P23BaseAdapter(JoyonwayBaseAdapter):
    """Base adapter for the Joyonway P23 model family."""

    model: str
    broadcast_signature: bytes = P23B32_SIGNATURE
    unescape_full_frame: bool = False  # Tail-only (full payload unescape corrupts data)
    supports_writes: bool = True
    jets: list[JetDescription]
    supported_light_colors: list[str] = []
    has_blower: bool = False

    heater_state_map: dict[int, str] = HEATER_STATE_MAP

    _cmd_prefix_byte = 0x30
    _cmd_context_flag = 0x00
    _mask_light = 0x01
    _context_byte: ClassVar[int] = 0x04

    def _post_parse_status(
        self,
        result: dict,
        frame: bytes,
        jet_byte: int,
        ozone_mode_byte: int,
        heater_byte: int,
    ) -> None:
        """Post-process status dictionary for P23 specifics."""
        result["jets_left"] = "on" if (jet_byte & MASK_JET_LEFT) else "off"
        result["jets_right"] = "on" if (jet_byte & MASK_JET_RIGHT) else "off"

        ozone_mode_manual = bool(ozone_mode_byte & MASK_OZONE_MODE_MANUAL)
        heater_mode_manual = bool(ozone_mode_byte & MASK_HEATER_MODE_MANUAL)
        result["ozone_mode"] = "manual" if ozone_mode_manual else "auto"
        result["heater_mode"] = "manual" if heater_mode_manual else "auto"

    def _build_button_command(
        self,
        jet_b7: int = 0x00,
        jet_b8: int = 0x00,
        btn_group: int = 0x00,
        btn_action: int = 0x00,
        modifier: int = 0x02,
        context: int | None = None,
        val_13: int = 0x00,
        setpoint_f: int = 0x00,
        tail_byte: int | None = None,
    ) -> bytes:
        """Build a button command for the P23 family."""
        from ..protocol import build_frame

        if context is None:
            context = self._context_byte

        payload = bytearray(
            [
                0x01,
                0x30,
                0x10,
                0x3C,
                0xA1,
                0x00,
                0xA1,
                jet_b7,
                jet_b8,
                btn_group,
                btn_action,
                modifier,
                context,
                val_13,
                setpoint_f,
            ]
        )
        if tail_byte is not None:
            payload.extend([0x00, tail_byte])
        else:
            payload.append(0x00)

        return build_frame(bytes(payload))

    def build_light_command(self, on: bool, color: str | None = None) -> bytes:
        """Build light command (unsupported in base P23)."""
        raise NotImplementedError("Discrete light controls not supported on this model")

    def build_jets_command(self, jet_id: str, target: str) -> bytes | None:
        is_on = target in ("low", "high", "on")

        if jet_id == "jets_left":
            if is_on:
                b7, b8 = 0x06, 0x04
            else:
                b7, b8 = 0x06, 0x00
        elif jet_id == "jets_right":
            if is_on:
                b7, b8 = 0x18, 0x10
            else:
                b7, b8 = 0x18, 0x00
        else:
            return None

        return self._build_button_command(jet_b7=b7, jet_b8=b8)

    def build_heater_command(self, on: bool) -> bytes:
        b10 = 0x18 if on else 0x11
        return self._build_button_command(
            btn_group=0x08,
            btn_action=b10,
        )

    def build_blower_command(self, on: bool) -> bytes:
        b10 = 0x04 if on else 0x00
        return self._build_button_command(
            btn_group=0x04,
            btn_action=b10,
        )

    def build_temp_command(self, target_celsius: int) -> bytes | None:
        if target_celsius < self.temp_min_c or target_celsius > self.temp_max_c:
            return None
        target_f = celsius_to_fahrenheit(target_celsius)
        return self._build_button_command(
            btn_group=0x80,
            btn_action=0x80,
            setpoint_f=target_f,
        )

    def build_ozone_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        raise NotImplementedError("Mode switching not supported on this model")

    def build_heater_mode_command(self, mode: str, setpoint_f: int = 0x62) -> bytes:
        raise NotImplementedError("Mode switching not supported on this model")

    def build_ozone_manual_command(self, on: bool, setpoint_f: int = 0x62) -> bytes:
        b10 = 0x01 if on else 0x10
        return self._build_button_command(
            btn_group=0x01,
            btn_action=b10,
        )


class P23B32Adapter(P23BaseAdapter):
    """Adapter for the Joyonway P23B32 controller."""

    model: str = "P23B32"
    broadcast_signature: bytes = P23B32_SIGNATURE
    unescape_full_frame: bool = False
    supports_writes: bool = True
    has_blower: bool = True
    jets: list[JetDescription] = [
        JetDescription(id="jets_left", name="Jets Left", type=JetType.SINGLE),
        JetDescription(id="jets_right", name="Jets Right", type=JetType.SINGLE),
    ]

    _context_byte = 0x04

    def build_light_command(self, on: bool, color: str | None = None) -> bytes:
        """Build a discrete light ON or OFF command for P23B32."""
        return self._build_button_command(
            btn_group=0x00,
            btn_action=0x40,
            modifier=0x40,
            context=0x02,
            val_13=0x04,
            tail_byte=0x81 if on else 0x80,
        )

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        return _P23B32_ENTITIES


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
        format_hex=True,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="jets_byte_raw",
        name="Jets byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
        format_hex=True,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="ozone_mode_byte_raw",
        name="Ozone mode byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
        format_hex=True,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="activity_byte_raw",
        name="Activity byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
        format_hex=True,
    ),
    SpaEntityDescription(
        platform="sensor",
        key="light_cycle_byte_raw",
        name="Light/cycle byte (raw)",
        icon="mdi:memory",
        entity_category="diagnostic",
        enabled_by_default=False,
        format_hex=True,
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
