"""P20 model family adapter — byte map and entity definitions.

Protocol differences from P23B32:
- Idle base offset is 0x20 instead of 0x40.
- Command prefix is 0x01 0x30 (same as P23).
- Custom 16-byte light command with preset color support.
- Custom ozone manual command.
- Ozone/heater manual configuration modes are not supported or verified on this model (returns None/b"").
"""

from __future__ import annotations

from typing import ClassVar

from .base import (
    JoyonwayBaseAdapter,
    JetDescription,
    JetType,
    SpaEntityDescription,
    celsius_to_fahrenheit,
)

# Broadcast header for P20 (bytes 0-8). Byte 7 is the board version, byte 8
# (0x01) is the family ID.
P20B29_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x01])

MASK_JET_LEFT = 0x04
MASK_JET_RIGHT = 0x10
MASK_HEATER_BLOWER = 0x08

LIGHT_COLOR_INDEX_TO_NAME: dict[int, str] = {
    1: "auto",
    2: "red",
    3: "green",
    4: "yellow",
    5: "blue",
    6: "purple",
    7: "cyan",
    8: "white",
}
LIGHT_COLOR_NAME_TO_INDEX: dict[str, int] = {
    v: k for k, v in LIGHT_COLOR_INDEX_TO_NAME.items()
}


class P20BaseAdapter(JoyonwayBaseAdapter):
    """Base adapter for the Joyonway P20 model family."""

    model: str
    broadcast_signature: bytes = P20B29_SIGNATURE
    unescape_full_frame: bool = (
        True  # Full-frame unescape verified on community captures
    )
    supports_writes: bool = True
    jets: list[JetDescription]
    supported_light_colors: list[str] = []
    has_blower: bool = False
    supports_mode_switching: bool = False

    heater_state_map: dict[int, str]

    _cmd_prefix_byte = 0x30
    _cmd_context_flag = 0x00
    _context_byte: ClassVar[int] = 0x04

    def color_index_to_name(self, index: int) -> str | None:
        """Map color index to name."""
        return LIGHT_COLOR_INDEX_TO_NAME.get(index)

    def color_name_to_index(self, name: str) -> int | None:
        """Map color name to index."""
        return LIGHT_COLOR_NAME_TO_INDEX.get(name)

    def _post_parse_status(
        self,
        result: dict,
        frame: bytes,
        jet_byte: int,
        ozone_mode_byte: int,
        heater_byte: int,
    ) -> None:
        """Post-process status dictionary for P20 specifics."""
        result["jets_left"] = "on" if (jet_byte & MASK_JET_LEFT) else "off"
        result["jets_right"] = "on" if (jet_byte & MASK_JET_RIGHT) else "off"
        result["ozone_active"] = bool(heater_byte & 0x01)

        # F3: Do not emit ozone_mode and heater_mode keys at all
        if "ozone_mode" in result:
            del result["ozone_mode"]
        if "heater_mode" in result:
            del result["heater_mode"]

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
    ) -> bytes:
        """Build a 16-byte button command for the P20 family."""
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
                0x00,
            ]
        )
        return build_frame(bytes(payload))

    def build_light_command(self, on: bool, color: str | None = None) -> bytes:
        """Build a discrete light command for P20."""
        from ..protocol import build_frame

        if not on:
            tail = 0x80
        elif color is not None:
            if color not in self.supported_light_colors:
                raise ValueError(f"Unsupported light color: {color}")
            tail = 0x80 + LIGHT_COLOR_NAME_TO_INDEX[color]
        else:
            tail = 0x81

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
                0x40,
                0x40,
                0x02,
                0x04,
                0x00,
                0x00,
                tail,
            ]
        )
        return build_frame(bytes(payload))

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
        b7 = 0x80
        b8 = 0x80 if on else 0x00
        return self._build_button_command(
            jet_b7=b7,
            jet_b8=b8,
            setpoint_f=setpoint_f,
        )


class P20B29Adapter(P20BaseAdapter):
    """Adapter for the Joyonway P20B29 controller."""

    model: str = "P20B29"
    broadcast_signature: bytes = P20B29_SIGNATURE
    unescape_full_frame: bool = True
    supports_writes: bool = True
    has_blower: bool = True
    jets: list[JetDescription] = [
        JetDescription(id="jets_left", name="Jets Left", type=JetType.SINGLE),
        JetDescription(id="jets_right", name="Jets Right", type=JetType.SINGLE),
    ]
    supported_light_colors: list[str] = [
        "auto",
        "red",
        "green",
        "yellow",
        "blue",
        "purple",
        "cyan",
        "white",
    ]

    heater_state_map = {
        0x20: "off",
        0x21: "circulation",
        0x24: "heating",
        0x25: "heating",
    }

    _context_byte = 0x04

    def entity_descriptions(self) -> list[SpaEntityDescription]:
        return _P20B29_ENTITIES


_P20B29_ENTITIES: list[SpaEntityDescription] = [
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
