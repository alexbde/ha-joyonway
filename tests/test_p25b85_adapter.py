"""Pytest coverage for protocol and P25B85 adapter behavior."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys
import types

import pytest

from _loader import load_module

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "joyonway_p25b85"

protocol = load_module("joyonway_p25b85.protocol", PKG_DIR / "protocol.py")
adapters_base = load_module(
    "joyonway_p25b85.adapters.base", PKG_DIR / "adapters" / "base.py"
)
adapters_pkg_mod = types.ModuleType("joyonway_p25b85.adapters")
adapters_pkg_mod.base = adapters_base
adapters_pkg_mod.SpaEntityDescription = adapters_base.SpaEntityDescription
sys.modules["joyonway_p25b85.adapters"] = adapters_pkg_mod
adapters_p25b85 = load_module(
    "joyonway_p25b85.adapters.p25b85", PKG_DIR / "adapters" / "p25b85.py"
)
adapters_registry = load_module(
    "joyonway_p25b85.adapters_init", PKG_DIR / "adapters" / "__init__.py"
)

find_frames = protocol.find_frames
pseudo_unescape = protocol.pseudo_unescape
unescape_frame = protocol.unescape_frame
is_broadcast = protocol.is_broadcast
validate_frame = protocol.validate_frame
build_frame = protocol.build_frame
FRAME_START = protocol.FRAME_START
FRAME_END = protocol.FRAME_END

P25B85Adapter = adapters_p25b85.P25B85Adapter
P25B85_SIGNATURE = adapters_p25b85.P25B85_SIGNATURE
IDX_HEATER_STATE = adapters_p25b85.IDX_HEATER_STATE
IDX_DATETIME_START = adapters_p25b85.IDX_DATETIME_START
HEATER_OFF = adapters_p25b85.HEATER_OFF
HEATER_HEATING = adapters_p25b85.HEATER_HEATING
HEATER_CIRCULATION = adapters_p25b85.HEATER_CIRCULATION
HEATER_OZONE = adapters_p25b85.HEATER_OZONE
fahrenheit_to_celsius = adapters_p25b85._fahrenheit_to_celsius
celsius_to_fahrenheit = adapters_p25b85._celsius_to_fahrenheit

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
def adapter() -> P25B85Adapter:
    return P25B85Adapter()


@pytest.fixture
def logical_frame() -> bytes:
    return unescape_frame(KDY_RAW, full=True)


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
    logical = unescape_frame(KDY_RAW, full=True)
    assert logical[0] == FRAME_START
    assert logical[-1] == FRAME_END


def test_adapter_properties(adapter: P25B85Adapter, logical_frame: bytes) -> None:
    assert adapter.model == "P25B85"
    assert adapter.unescape_full_frame is True
    assert adapter.supports_writes is True
    assert logical_frame[: len(P25B85_SIGNATURE)] == P25B85_SIGNATURE


def test_parse_status_core_fields(adapter: P25B85Adapter, logical_frame: bytes) -> None:
    result = adapter.parse_status(logical_frame)
    assert isinstance(result, dict)
    assert result["water_temperature"] == 34
    assert result["setpoint"] == 40
    assert result["status"] == "off"
    assert result["heater_active"] is False
    assert result["pump_high"] is True
    assert result["pump_low"] is False
    assert result["light"] is True


def test_parse_status_datetime(adapter: P25B85Adapter, logical_frame: bytes) -> None:
    modified = bytearray(logical_frame)
    modified[IDX_DATETIME_START : IDX_DATETIME_START + 6] = bytes([24, 5, 20, 14, 30, 45])
    result = adapter.parse_status(bytes(modified))
    assert isinstance(result["spa_datetime"], datetime)
    assert result["spa_datetime"].tzinfo == timezone.utc


def test_parse_rejects_wrong_signature(adapter: P25B85Adapter, logical_frame: bytes) -> None:
    modified = bytearray(logical_frame)
    modified[8] = 0x02
    assert adapter.parse_status(bytes(modified)) is None


@pytest.mark.parametrize(
    ("heater_byte", "state", "active", "ozone"),
    [
        (HEATER_OFF, "off", False, False),
        (HEATER_CIRCULATION, "circulation", False, False),
        (HEATER_HEATING, "heating", True, False),
        (0x54, "heating", True, False),
        (HEATER_OZONE, "ozone", False, True),
        (0xC1, "ozone", False, True),
        (0x99, "unknown", False, False),
    ],
)
def test_heater_state_mapping(
    adapter: P25B85Adapter,
    logical_frame: bytes,
    heater_byte: int,
    state: str,
    active: bool,
    ozone: bool,
) -> None:
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = heater_byte
    result = adapter.parse_status(bytes(modified))
    assert result["status"] == state
    assert result["heater_active"] is active
    assert result["ozone_active"] is ozone


def test_entity_descriptions(adapter: P25B85Adapter) -> None:
    descs = adapter.entity_descriptions()
    assert descs
    assert {d.platform for d in descs} >= {"sensor"}
    assert "water_temperature" in {d.key for d in descs}
    assert "status" in {d.key for d in descs}
    assert "jets" in {d.key for d in descs}


def test_adapter_registry() -> None:
    assert "P25B85" in ADAPTERS
    assert get_adapter("P25B85").model == "P25B85"
    with pytest.raises(ValueError):
        get_adapter("UNKNOWN")


@pytest.mark.parametrize(
    ("f", "expected"),
    [(94, 34), (104, 40), (32, 0), (0, None), (201, None), (255, None)],
)
def test_fahrenheit_to_celsius(f: int, expected: int | None) -> None:
    assert fahrenheit_to_celsius(f) == expected


def test_parse_schedule_from_live_frame(adapter: P25B85Adapter) -> None:
    """Test schedule parsing from an actual captured broadcast frame."""
    frame = bytes.fromhex(
        "1aff013cd2b4ff0803600006007d40006200004b001000140016"
        "0000004b000c00510012000000064d0000000000000000000000"
        "001a0517160e2506009db678a21d"
    )
    unescaped = unescape_frame(frame, full=True)
    result = adapter.parse_status(unescaped)

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


def test_build_schedule_command(adapter: P25B85Adapter) -> None:
    """Test building schedule command frames with CRC."""
    frame = adapter.build_schedule_command(
        "heat",
        slot1_start=(12, 0),
        slot1_end=(16, 0),
        slot2_start=(20, 0),
        slot2_end=(22, 0),
    )
    # Frame must start with 0x1A and end with 0x1D
    assert frame[0] == 0x1A
    assert frame[-1] == 0x1D
    # Must be a valid wire frame
    assert len(frame) >= 22  # 1 + 20 (escaped) + 1

    # Verify payload structure
    inner = pseudo_unescape(frame[1:-1])
    payload = inner[:16]
    assert payload[4] == 0xA3  # heat schedule
    # Default: both slots enabled → flags = 0xAA
    assert payload[7] == 0xAA
    # Check times in payload
    assert payload[8] == 12   # slot1 start hour
    assert payload[9] == 0    # slot1 start minute
    assert payload[10] == 16  # slot1 end hour
    assert payload[11] == 0   # slot1 end minute
    assert payload[12] == 20  # slot2 start hour
    assert payload[13] == 0   # slot2 start minute
    assert payload[14] == 22  # slot2 end hour
    assert payload[15] == 0   # slot2 end minute

    # Build a filter schedule command with both slots enabled
    frame2 = adapter.build_schedule_command(
        "filter",
        slot1_start=(12, 0),
        slot1_end=(12, 0),
        slot2_start=(17, 0),
        slot2_end=(18, 0),
    )
    assert frame2[0] == 0x1A
    assert frame2[-1] == 0x1D
    inner2 = pseudo_unescape(frame2[1:-1])
    assert inner2[4] == 0xA4  # filter schedule
    assert inner2[7] == 0xAA  # both enabled


def test_build_schedule_command_enable_flags(adapter: P25B85Adapter) -> None:
    """Test that the flags byte correctly encodes slot enable state."""
    times = dict(
        slot1_start=(12, 0), slot1_end=(16, 0),
        slot2_start=(20, 0), slot2_end=(22, 0),
    )

    # Both enabled → 0xAA
    frame = adapter.build_schedule_command("heat", **times, slot1_enabled=True, slot2_enabled=True)
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0xAA

    # Slot 1 on, slot 2 off → 0x62 (confirmed Phase 6: heat_schedule_disable)
    frame = adapter.build_schedule_command("heat", **times, slot1_enabled=True, slot2_enabled=False)
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x62

    # Slot 1 off, slot 2 on → 0x9A (confirmed Phase 6: heat_schedule_change)
    frame = adapter.build_schedule_command("heat", **times, slot1_enabled=False, slot2_enabled=True)
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x9A

    # Both disabled → 0x52
    frame = adapter.build_schedule_command("heat", **times, slot1_enabled=False, slot2_enabled=False)
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x52

    # Same encoding for filter
    frame = adapter.build_schedule_command("filter", **times, slot1_enabled=True, slot2_enabled=False)
    inner = pseudo_unescape(frame[1:-1])
    assert inner[7] == 0x62


def test_build_schedule_command_phase6_match(adapter: P25B85Adapter) -> None:
    """Verify build_schedule_command matches Phase 6 captured frames byte-for-byte."""
    # Phase 6 capture: heat_schedule_enable — both slots enabled
    # Wire: 1a0120103ca310a1aa0c001000150016003efb8dd91d
    frame = adapter.build_schedule_command(
        "heat",
        slot1_start=(12, 0), slot1_end=(16, 0),
        slot2_start=(21, 0), slot2_end=(22, 0),
        slot1_enabled=True, slot2_enabled=True,
    )
    assert frame == bytes.fromhex("1a0120103ca310a1aa0c001000150016003efb8dd91d")

    # Phase 6 capture: heat_schedule_disable (slot2 disabled)
    # Wire: 1a0120103ca310a1620c00100015001600e09a71e91d
    frame = adapter.build_schedule_command(
        "heat",
        slot1_start=(12, 0), slot1_end=(16, 0),
        slot2_start=(21, 0), slot2_end=(22, 0),
        slot1_enabled=True, slot2_enabled=False,
    )
    assert frame == bytes.fromhex("1a0120103ca310a1620c00100015001600e09a71e91d")

    # Phase 6 capture: filter_schedule_enable (both enabled)
    # Wire: 1a0120103ca410a1aa0b000d00110012007e2109021d
    frame = adapter.build_schedule_command(
        "filter",
        slot1_start=(11, 0), slot1_end=(13, 0),
        slot2_start=(17, 0), slot2_end=(18, 0),
        slot1_enabled=True, slot2_enabled=True,
    )
    assert frame == bytes.fromhex("1a0120103ca410a1aa0b000d00110012007e2109021d")

    # Phase 6 capture: filter_schedule_disable (slot2 disabled)
    # Wire: 1a0120103ca410a1620b000d0011001200a040f5321d
    frame = adapter.build_schedule_command(
        "filter",
        slot1_start=(11, 0), slot1_end=(13, 0),
        slot2_start=(17, 0), slot2_end=(18, 0),
        slot1_enabled=True, slot2_enabled=False,
    )
    assert frame == bytes.fromhex("1a0120103ca410a1620b000d0011001200a040f5321d")


def test_build_schedule_command_rejects_invalid_type(adapter: P25B85Adapter) -> None:
    with pytest.raises(ValueError):
        adapter.build_schedule_command(
            "invalid",
            slot1_start=(12, 0),
            slot1_end=(16, 0),
            slot2_start=(20, 0),
            slot2_end=(22, 0),
        )


def test_build_datetime_command(adapter: P25B85Adapter) -> None:
    """Test building datetime command frames — verify against captured frames."""
    # Captured session 2: 2026-05-21 22:53:00 (time-only, prefix=0x50)
    # Wire: 1a0120103ca210a1501b110515163500000087ecf6541d
    # Note: 0x1A in payload gets escaped to 1B 11 on wire
    frame = adapter.build_datetime_command(2026, 5, 21, 22, 53, 0, set_date=False)
    assert frame[0] == 0x1A
    assert frame[-1] == 0x1D

    # Verify it matches the captured frame exactly
    captured = "1a0120103ca210a1501b110515163500000087ecf6541d"
    assert frame.hex() == captured

    # Captured session 1: 2026-05-21 15:09:00 (time-only, prefix=0x50)
    # Wire: 1a0120103ca210a1501b1105150f090000004cbc3d971d
    frame2 = adapter.build_datetime_command(2026, 5, 21, 15, 9, 0, set_date=False)
    captured2 = "1a0120103ca210a1501b1105150f090000004cbc3d971d"
    assert frame2.hex() == captured2

    # Captured from PB554 panel date change: 2025-04-26 23:10:00 (date+time, prefix=0x05)
    # Wire: 1a0120103ca210a10519041b11170a000000e0b873261d
    frame3 = adapter.build_datetime_command(2025, 4, 26, 23, 10, 0, set_date=True)
    captured3 = "1a0120103ca210a10519041b11170a000000e0b873261d"
    assert frame3.hex() == captured3


# ── Dynamic command builder tests ────────────────────────────


def _frame_payload(frame: bytes) -> bytes:
    """Extract the 16-byte unescaped payload from a wire frame."""
    return pseudo_unescape(frame[1:-1])[:16]


def test_build_light_toggle(adapter: P25B85Adapter) -> None:
    """Light toggle command has correct structure."""
    frame = adapter.build_light_toggle_command()
    assert frame[0] == 0x1A and frame[-1] == 0x1D
    p = _frame_payload(frame)
    assert p[4] == 0xA1  # button command type
    assert p[9] == 0x40  # btn_group = light
    assert p[10] == 0x40  # btn_action = toggle


def test_build_pump_commands(adapter: P25B85Adapter) -> None:
    """Pump transition commands encode correct bytes 7-8."""
    f1 = adapter.build_pump_command("off", "low")
    assert f1 is not None
    p1 = _frame_payload(f1)
    assert p1[7] == 0x02 and p1[8] == 0x02

    f2 = adapter.build_pump_command("low", "high")
    p2 = _frame_payload(f2)
    assert p2[7] == 0x06 and p2[8] == 0x04

    f3 = adapter.build_pump_command("high", "off")
    p3 = _frame_payload(f3)
    assert p3[7] == 0x04 and p3[8] == 0x00

    assert adapter.build_pump_command("off", "high") is not None
    f4 = adapter.build_pump_command("off", "high")
    p4 = _frame_payload(f4)
    assert p4[7] == 0x06 and p4[8] == 0x04

    assert adapter.build_pump_command("low", "off") is not None
    assert adapter.build_pump_command("high", "low") is not None
    assert adapter.build_pump_command("low", "low") is None


def test_build_heater_commands(adapter: P25B85Adapter) -> None:
    """Heater ON/OFF commands have correct btn_group and btn_action."""
    on_frame = adapter.build_heater_command(on=True)
    p = _frame_payload(on_frame)
    assert p[9] == 0x08 and p[10] == 0x08

    off_frame = adapter.build_heater_command(on=False)
    p = _frame_payload(off_frame)
    assert p[9] == 0x08 and p[10] == 0x00


def test_build_blower_commands(adapter: P25B85Adapter) -> None:
    """Blower ON/OFF commands have correct btn_group and btn_action."""
    on_frame = adapter.build_blower_command(on=True)
    p = _frame_payload(on_frame)
    assert p[9] == 0x04 and p[10] == 0x0C

    off_frame = adapter.build_blower_command(on=False)
    p = _frame_payload(off_frame)
    assert p[9] == 0x04 and p[10] == 0x00


def test_build_temp_command(adapter: P25B85Adapter) -> None:
    """Temperature command encodes °F correctly in byte 14."""
    # 20°C → 68°F
    frame = adapter.build_temp_command(20)
    assert frame is not None
    p = _frame_payload(frame)
    assert p[9] == 0x80  # btn_group = temperature
    assert p[10] == 0x98  # btn_action (confirmed working via live test)
    assert p[14] == 68  # 20°C = 68°F

    # 40°C → 104°F
    frame = adapter.build_temp_command(40)
    p = _frame_payload(frame)
    assert p[14] == 104

    # Out of range
    assert adapter.build_temp_command(5) is None
    assert adapter.build_temp_command(45) is None


def test_build_ozone_mode_commands(adapter: P25B85Adapter) -> None:
    """Ozone mode switch commands have correct structure."""
    auto_frame = adapter.build_ozone_mode_command("auto")
    p = _frame_payload(auto_frame)
    assert p[9] == 0x00  # btn_group
    assert p[10] == 0x00  # btn_action
    assert p[11] == 0x80  # modifier (ozone mode)
    assert p[12] == 0xC0  # context = auto

    manual_frame = adapter.build_ozone_mode_command("manual")
    p = _frame_payload(manual_frame)
    assert p[11] == 0x80
    assert p[12] == 0x40  # context = manual

    with pytest.raises(ValueError):
        adapter.build_ozone_mode_command("invalid")


def test_build_ozone_manual_commands(adapter: P25B85Adapter) -> None:
    """Ozone manual ON/OFF commands have correct structure."""
    on_frame = adapter.build_ozone_manual_command(on=True)
    p = _frame_payload(on_frame)
    assert p[9] == 0x01  # btn_group = ozone manual
    assert p[10] == 0x01  # btn_action = ON
    assert p[12] == 0x40  # context = manual mode

    off_frame = adapter.build_ozone_manual_command(on=False)
    p = _frame_payload(off_frame)
    assert p[9] == 0x01
    assert p[10] == 0x10  # btn_action = OFF
    assert p[12] == 0x40


def test_build_ozone_commands_match_phase6(adapter: P25B85Adapter) -> None:
    """Verify ozone commands match Phase 6 captured frames byte-for-byte.

    Captured frames used setpoint 0x62 (98°F).
    """
    # Ozone mode → Manual
    frame = adapter.build_ozone_mode_command("manual", setpoint_f=0x62)
    assert frame == bytes.fromhex("1a0120103ca110a10000000080400062003a8412c71d")

    # Ozone manual ON
    frame = adapter.build_ozone_manual_command(on=True, setpoint_f=0x62)
    assert frame == bytes.fromhex("1a0120103ca110a100000101004000620060b46dea1d")

    # Ozone manual OFF
    frame = adapter.build_ozone_manual_command(on=False, setpoint_f=0x62)
    assert frame == bytes.fromhex("1a0120103ca110a1000001100040006200bd2b48431d")


def test_celsius_to_fahrenheit() -> None:
    """Test C→F conversion."""
    assert celsius_to_fahrenheit(0) == 32
    assert celsius_to_fahrenheit(100) == 212
    assert celsius_to_fahrenheit(20) == 68
    assert celsius_to_fahrenheit(40) == 104

