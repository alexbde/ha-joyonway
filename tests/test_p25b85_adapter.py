"""Pytest coverage for protocol and P25B85 adapter behaviors."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import types

import pytest

from _loader import load_module

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "joyonway"

protocol = load_module("joyonway.protocol", PKG_DIR / "protocol.py")
adapters_base = load_module("joyonway.adapters.base", PKG_DIR / "adapters" / "base.py")
adapters_pkg_mod = types.ModuleType("joyonway.adapters")
adapters_pkg_mod.base = adapters_base
adapters_pkg_mod.SpaEntityDescription = adapters_base.SpaEntityDescription
sys.modules["joyonway.adapters"] = adapters_pkg_mod
adapters_p25 = load_module("joyonway.adapters.p25", PKG_DIR / "adapters" / "p25.py")
adapters_registry = load_module(
    "joyonway.adapters_init", PKG_DIR / "adapters" / "__init__.py"
)

find_frames = protocol.find_frames
pseudo_unescape = protocol.pseudo_unescape
unescape_frame = protocol.unescape_frame
is_broadcast = protocol.is_broadcast
validate_frame = protocol.validate_frame
build_frame = protocol.build_frame
FRAME_START = protocol.FRAME_START
FRAME_END = protocol.FRAME_END

P25B85Adapter = adapters_p25.P25B85Adapter
P25_SIGNATURE = adapters_p25.P25_SIGNATURE
IDX_HEATER_STATE = adapters_p25.IDX_HEATER_STATE
IDX_JET_BYTE = adapters_p25.IDX_JET_BYTE
IDX_DATETIME_START = adapters_p25.IDX_DATETIME_START
HEATER_OFF = adapters_p25.HEATER_OFF
HEATER_HEATING = adapters_p25.HEATER_HEATING
HEATER_STANDBY = adapters_p25.HEATER_STANDBY
HEATER_OZONE = adapters_p25.HEATER_OZONE
fahrenheit_to_celsius = adapters_p25._fahrenheit_to_celsius
celsius_to_fahrenheit = adapters_p25._celsius_to_fahrenheit
IDX_LIGHT_CYCLE = adapters_p25.IDX_LIGHT_CYCLE
MASK_HEATING_CYCLE = adapters_p25.MASK_HEATING_CYCLE

get_adapter = adapters_registry.get_adapter
ADAPTERS = adapters_registry.ADAPTERS

KDY_RAW = bytes.fromhex(
    "1AFF013CD2B4FF08035E040604F54000"
    "6801001221123B140016000400430004"
    "3B120014000000064D00000000000000"
    "00000000001005081B1B111200004E28"
    "331D"
)


@pytest.fixture
def b85_adapter() -> P25B85Adapter:
    return P25B85Adapter()


@pytest.fixture
def logical_frame() -> bytes:
    return unescape_frame(KDY_RAW, unescape_full=True)


def test_find_frames_and_broadcast_validation() -> None:
    frames = find_frames(bytes([0xFF, 0x1A, 0xFF, 0x01, 0x1D]))
    assert len(frames) == 1
    assert is_broadcast(frames[0])
    assert validate_frame(frames[0])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (bytes([0x1B, 0x11]), bytes([0x1A])),
        (bytes([0x1B, 0x0B]), bytes([0x1B])),
        (bytes([0x1B, 0x13]), bytes([0x1C])),
        (bytes([0x1B, 0x14]), bytes([0x1D])),
        (bytes([0x1B, 0x15]), bytes([0x1E])),
    ],
)
def test_pseudo_unescape(raw: bytes, expected: bytes) -> None:
    assert pseudo_unescape(raw) == expected


def test_unescape_preserves_frame_delimiters() -> None:
    logical = unescape_frame(KDY_RAW, unescape_full=True)
    assert logical[0] == FRAME_START
    assert logical[-1] == FRAME_END


def test_adapter_properties(b85_adapter: P25B85Adapter, logical_frame: bytes) -> None:
    assert b85_adapter.model == "P25B85"
    assert b85_adapter.unescape_full_frame is True
    assert b85_adapter.supports_writes is True
    assert b85_adapter.has_blower is True
    assert logical_frame[: len(P25_SIGNATURE)] == P25_SIGNATURE


def test_parse_status_core_fields(
    b85_adapter: P25B85Adapter, logical_frame: bytes
) -> None:
    result = b85_adapter.parse_status(logical_frame)
    assert isinstance(result, dict)
    assert result["current_temperature"] == 34
    assert result["setpoint"] == 40
    assert result["status"] == "off"
    assert result["heater_active"] is False
    assert result["jet_high"] is True
    assert result["jet_low"] is False
    assert result["light"] is True
    assert result["ozone_mode"] == "manual"
    assert result["heater_mode"] == "manual"

    # Test auto modes when bits are cleared
    modified = bytearray(logical_frame)
    modified[13] = 0x00
    result_auto = b85_adapter.parse_status(bytes(modified))
    assert result_auto["ozone_mode"] == "auto"
    assert result_auto["heater_mode"] == "auto"


def test_parse_status_datetime(
    b85_adapter: P25B85Adapter, logical_frame: bytes
) -> None:
    modified = bytearray(logical_frame)
    modified[IDX_DATETIME_START : IDX_DATETIME_START + 6] = bytes(
        [24, 5, 20, 14, 30, 45]
    )
    result = b85_adapter.parse_status(bytes(modified))
    assert isinstance(result["spa_datetime"], datetime)
    assert result["spa_datetime"].tzinfo == timezone.utc


def test_parse_rejects_wrong_signature(
    b85_adapter: P25B85Adapter, logical_frame: bytes
) -> None:
    modified = bytearray(logical_frame)
    modified[8] = 0x02
    assert b85_adapter.parse_status(bytes(modified)) is None


@pytest.mark.parametrize(
    ("heater_byte", "state", "active", "ozone"),
    [
        (HEATER_OFF, "off", False, False),
        (HEATER_STANDBY, "standby", False, False),
        (HEATER_HEATING, "heating", True, False),
        (0x54, "heating", True, False),
        (HEATER_OZONE, "ozone", False, True),
        (0xC1, "ozone", False, True),
        (0x99, "unknown", False, False),
    ],
)
def test_heater_state_mapping(
    b85_adapter: P25B85Adapter,
    logical_frame: bytes,
    heater_byte: int,
    state: str,
    active: bool,
    ozone: bool,
) -> None:
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = heater_byte
    result = b85_adapter.parse_status(bytes(modified))
    assert result["status"] == state
    assert result["heater_active"] is active
    assert result["ozone_active"] is ozone


def test_standby_status_when_pump_off(
    b85_adapter: P25B85Adapter,
    logical_frame: bytes,
) -> None:
    """Status is 'standby' when heater byte is 0x50 (heater armed)."""
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = HEATER_STANDBY
    modified[IDX_JET_BYTE] = 0x00
    result = b85_adapter.parse_status(bytes(modified))
    assert result["status"] == "standby"


def test_standby_status_even_when_jets_running(
    b85_adapter: P25B85Adapter,
    logical_frame: bytes,
) -> None:
    """Status is 'standby' when heater byte is 0x50 even with manual jets active."""
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = HEATER_STANDBY
    modified[IDX_JET_BYTE] = 0x02  # manual jets low
    result = b85_adapter.parse_status(bytes(modified))
    assert result["status"] == "standby"


def test_preheat_circulation_status(
    b85_adapter: P25B85Adapter,
    logical_frame: bytes,
) -> None:
    """Status is 'circulation' when heater byte is 0x50 but heating cycle is active."""
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = HEATER_STANDBY
    modified[IDX_LIGHT_CYCLE] = MASK_HEATING_CYCLE
    result = b85_adapter.parse_status(bytes(modified))
    assert result["status"] == "circulation"


def test_entity_descriptions(b85_adapter: P25B85Adapter) -> None:
    descs = b85_adapter.entity_descriptions()
    assert descs
    assert {d.platform for d in descs} >= {"sensor"}
    assert "current_temperature" in {d.key for d in descs}
    assert "status" in {d.key for d in descs}
    assert "jets" in {d.key for d in descs}


def test_adapter_registry() -> None:
    assert "P25B85" in ADAPTERS
    assert get_adapter("P25B85").model == "P25B85"


@pytest.mark.parametrize(
    ("f", "expected"),
    [(94, 34), (104, 40), (32, 0), (0, None), (201, None), (255, None)],
)
def test_fahrenheit_to_celsius(f: int, expected: int | None) -> None:
    assert fahrenheit_to_celsius(f) == expected


def test_parse_schedule_from_live_frame(b85_adapter: P25B85Adapter) -> None:
    """Test schedule parsing from an actual captured broadcast frame."""
    frame = bytes.fromhex(
        "1aff013cd2b4ff0803600006007d40006200004b001000140016"
        "0000004b000c00510012000000064d0000000000000000000000"
        "001a0517160e2506009db678a21d"
    )
    unescaped = unescape_frame(frame, unescape_full=True)
    result = b85_adapter.parse_status(unescaped)

    assert result["heat_slot1_start"] == (11, 0)
    assert result["heat_slot1_end"] == (16, 0)
    assert result["heat_slot1_enabled"] is True
    assert result["heat_slot2_start"] == (20, 0)
    assert result["heat_slot2_end"] == (22, 0)
    assert result["heat_slot2_enabled"] is False

    assert result["filter_slot1_start"] == (11, 0)
    assert result["filter_slot1_end"] == (12, 0)
    assert result["filter_slot1_enabled"] is True
    assert result["filter_slot2_start"] == (17, 0)
    assert result["filter_slot2_end"] == (18, 0)
    assert result["filter_slot2_enabled"] is True

    assert result["spa_datetime"].year == 2026
    assert result["spa_datetime"].month == 5
    assert result["spa_datetime"].day == 23


def test_build_schedule_command(b85_adapter: P25B85Adapter) -> None:
    """Test building schedule command frames with CRC."""
    frame = b85_adapter.build_schedule_command(
        "heat",
        slot1_start=(12, 0),
        slot1_end=(16, 0),
        slot2_start=(20, 0),
        slot2_end=(22, 0),
    )
    assert frame[0] == 0x1A
    assert frame[-1] == 0x1D
    assert len(frame) >= 22

    inner = pseudo_unescape(frame[1:-1])
    payload = inner[:16]
    assert payload[4] == 0xA3
    assert payload[7] == 0xAA
    assert payload[8] == 12
    assert payload[9] == 0
    assert payload[10] == 16
    assert payload[11] == 0
    assert payload[12] == 20
    assert payload[13] == 0
    assert payload[14] == 22
    assert payload[15] == 0


def test_build_schedule_command_enable_flags(b85_adapter: P25B85Adapter) -> None:
    times = dict(
        slot1_start=(12, 0),
        slot1_end=(16, 0),
        slot2_start=(20, 0),
        slot2_end=(22, 0),
    )

    frame = b85_adapter.build_schedule_command(
        "heat", **times, slot1_enabled=True, slot2_enabled=True
    )
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0xAA

    frame = b85_adapter.build_schedule_command(
        "heat", **times, slot1_enabled=True, slot2_enabled=False
    )
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x62


def test_build_schedule_command_phase6_match(b85_adapter: P25B85Adapter) -> None:
    frame = b85_adapter.build_schedule_command(
        "heat",
        slot1_start=(12, 0),
        slot1_end=(16, 0),
        slot2_start=(21, 0),
        slot2_end=(22, 0),
        slot1_enabled=True,
        slot2_enabled=True,
    )
    assert frame == bytes.fromhex("1a0120103ca310a1aa0c001000150016003efb8dd91d")


def test_build_schedule_command_time_write_flags(b85_adapter: P25B85Adapter) -> None:
    times = dict(
        slot1_start=(12, 0),
        slot1_end=(16, 0),
        slot2_start=(20, 0),
        slot2_end=(22, 0),
    )

    frame = b85_adapter.build_schedule_command(
        "heat",
        **times,
        slot1_enabled=False,
        slot2_enabled=False,
        write_mode="time",
    )
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x5A

    frame = b85_adapter.build_schedule_command(
        "heat",
        **times,
        slot1_enabled=True,
        slot2_enabled=False,
        write_mode="time",
    )
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x6A


def test_build_datetime_command(b85_adapter: P25B85Adapter) -> None:
    frame = b85_adapter.build_datetime_command(2026, 5, 21, 22, 53, 0, set_date=False)
    assert frame.hex() == "1a0120103ca210a1501b110515163500000087ecf6541d"


def _frame_payload(frame: bytes) -> bytes:
    return pseudo_unescape(frame[1:-1])[:16]


def test_build_light(b85_adapter: P25B85Adapter) -> None:
    # ON command -> context=0x40, tail_byte=0x81
    frame_on = b85_adapter.build_light_command(on=True)
    p_on = _frame_payload(frame_on)
    assert p_on[9] == 0x40  # btn_group
    assert p_on[10] == 0x40  # btn_action
    assert p_on[12] == 0x40  # context is 0x40 for light commands
    assert p_on[15] == 0x81  # tail_byte for ON (Auto)

    # OFF command -> context=0x40, tail_byte=0x80
    frame_off = b85_adapter.build_light_command(on=False)
    p_off = _frame_payload(frame_off)
    assert p_off[9] == 0x40
    assert p_off[10] == 0x40
    assert p_off[12] == 0x40
    assert p_off[15] == 0x80  # tail_byte for OFF

    # Verify colors not supported
    assert b85_adapter.supported_light_colors == []
    with pytest.raises(ValueError):
        b85_adapter.build_light_command(on=True, color="red")


def test_build_jets_commands(b85_adapter: P25B85Adapter) -> None:
    f_low = b85_adapter.build_jets_command("jets", "low")
    assert f_low is not None
    p_low = _frame_payload(f_low)
    assert p_low[7] == 0x02 and p_low[8] == 0x02


def test_build_heater_commands(b85_adapter: P25B85Adapter) -> None:
    on_frame = b85_adapter.build_heater_command(on=True)
    p = _frame_payload(on_frame)
    assert p[9] == 0x08 and p[10] == 0x08


def test_build_blower_commands(b85_adapter: P25B85Adapter) -> None:
    on_frame = b85_adapter.build_blower_command(on=True)
    p = _frame_payload(on_frame)
    assert p[9] == 0x04 and p[10] == 0x0C


def test_build_temp_command(b85_adapter: P25B85Adapter) -> None:
    frame = b85_adapter.build_temp_command(20)
    assert frame is not None
    p = _frame_payload(frame)
    assert p[9] == 0x80
    assert p[10] == 0x98
    assert p[14] == 68


def test_build_ozone_mode_commands(b85_adapter: P25B85Adapter) -> None:
    auto_frame = b85_adapter.build_ozone_mode_command("auto")
    p = _frame_payload(auto_frame)
    assert p[12] == 0xC0


def test_build_heater_mode_commands(b85_adapter: P25B85Adapter) -> None:
    auto_frame = b85_adapter.build_heater_mode_command("auto")
    p = _frame_payload(auto_frame)
    assert p[12] == 0x80


def test_build_ozone_manual_commands(b85_adapter: P25B85Adapter) -> None:
    on_frame = b85_adapter.build_ozone_manual_command(on=True)
    p = _frame_payload(on_frame)
    assert p[10] == 0x01


def test_celsius_to_fahrenheit() -> None:
    assert celsius_to_fahrenheit(20) == 68


def test_parse_status_diagnostics(
    b85_adapter: P25B85Adapter, logical_frame: bytes
) -> None:
    result = b85_adapter.parse_status(logical_frame)
    assert isinstance(result, dict)
    assert result["frame_length"] == len(logical_frame)
