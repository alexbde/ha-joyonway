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
FRAME_START = protocol.FRAME_START
FRAME_END = protocol.FRAME_END

P25B85Adapter = adapters_p25b85.P25B85Adapter
P25B85_SIGNATURE = adapters_p25b85.P25B85_SIGNATURE
IDX_HEATER_STATE = adapters_p25b85.IDX_HEATER_STATE
IDX_DATETIME_START = adapters_p25b85.IDX_DATETIME_START
HEATER_OFF = adapters_p25b85.HEATER_OFF
HEATER_HEATING = adapters_p25b85.HEATER_HEATING
HEATER_CIRCULATION = adapters_p25b85.HEATER_CIRCULATION
HEATER_DISINFECTION = adapters_p25b85.HEATER_DISINFECTION
fahrenheit_to_celsius = adapters_p25b85._fahrenheit_to_celsius

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
    assert result["heater_state"] == "off"
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
    ("heater_byte", "state", "active", "disinfection"),
    [
        (HEATER_OFF, "off", False, False),
        (HEATER_CIRCULATION, "circulation", False, False),
        (HEATER_HEATING, "heating", True, False),
        (0x54, "heating", True, False),
        (HEATER_DISINFECTION, "disinfection", False, True),
        (0xC1, "disinfection", False, True),
        (0x99, "unknown", False, False),
    ],
)
def test_heater_state_mapping(
    adapter: P25B85Adapter,
    logical_frame: bytes,
    heater_byte: int,
    state: str,
    active: bool,
    disinfection: bool,
) -> None:
    modified = bytearray(logical_frame)
    modified[IDX_HEATER_STATE] = heater_byte
    result = adapter.parse_status(bytes(modified))
    assert result["heater_state"] == state
    assert result["heater_active"] is active
    assert result["disinfection_active"] is disinfection


def test_entity_descriptions(adapter: P25B85Adapter) -> None:
    descs = adapter.entity_descriptions()
    assert descs
    assert {d.platform for d in descs} >= {"sensor"}
    assert "water_temperature" in {d.key for d in descs}
    assert "heater_state" in {d.key for d in descs}
    assert "pump_state" in {d.key for d in descs}


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
    # Real frame captured from spa:
    # Heat: slot1=11:00-16:00 (enabled), slot2=20:00-22:00 (disabled)
    # Filter: slot1=11:00-12:00 (enabled), slot2=17:00-18:00 (enabled)
    frame = bytes.fromhex(
        "1aff013cd2b4ff0803600006007d40006200004b001000140016"
        "0000004b000c00510012000000064d0000000000000000000000"
        "001a0517160e2506009db678a21d"
    )
    unescaped = unescape_frame(frame, full=True)
    result = adapter.parse_status(unescaped)

    # Heat schedule — byte 19=0x4B: hour=11, enabled; byte 23=0x14: hour=20, disabled
    assert result["heat_slot1_start"] == (11, 0)
    assert result["heat_slot1_end"] == (16, 0)
    assert result["heat_slot1_enabled"] is True
    assert result["heat_slot2_start"] == (20, 0)
    assert result["heat_slot2_end"] == (22, 0)
    assert result["heat_slot2_enabled"] is False

    # Filter schedule — byte 29=0x4B: hour=11, enabled; byte 33=0x51: hour=17, enabled
    assert result["filter_slot1_start"] == (11, 0)
    assert result["filter_slot1_end"] == (12, 0)
    assert result["filter_slot1_enabled"] is True
    assert result["filter_slot2_start"] == (17, 0)
    assert result["filter_slot2_end"] == (18, 0)
    assert result["filter_slot2_enabled"] is True

    # Datetime
    assert result["spa_datetime"].year == 2026
    assert result["spa_datetime"].month == 5
    assert result["spa_datetime"].day == 23


def test_build_schedule_command(adapter: P25B85Adapter) -> None:
    """Test building schedule command frames with CRC."""
    # Build a heat schedule command matching the captured frame
    # Captured: heat slot1 start=12:00, end=16:00, slot2 start=20:00, end=22:00
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

    # Build a filter schedule command
    frame2 = adapter.build_schedule_command(
        "filter",
        slot1_start=(12, 0),
        slot1_end=(12, 0),
        slot2_start=(17, 0),
        slot2_end=(18, 0),
    )
    assert frame2[0] == 0x1A
    assert frame2[-1] == 0x1D

    # Verify the captured heat schedule frame matches our generation
    # Captured session 2: payload 0120103ca310a1620c001000140016 00
    # Our frame with same times should produce the same payload
    inner = pseudo_unescape(frame[1:-1])
    payload = inner[:16]
    # Check command type byte
    assert payload[4] == 0xA3  # heat schedule
    # Check times in payload
    assert payload[8] == 12   # slot1 start hour
    assert payload[9] == 0    # slot1 start minute
    assert payload[10] == 16  # slot1 end hour
    assert payload[11] == 0   # slot1 end minute
    assert payload[12] == 20  # slot2 start hour
    assert payload[13] == 0   # slot2 start minute
    assert payload[14] == 22  # slot2 end hour
    assert payload[15] == 0   # slot2 end minute


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
    # Captured session 2: 2026-05-21 22:53:00
    # Wire: 1a0120103ca210a1501b110515163500000087ecf6541d
    # Note: 0x1A in payload gets escaped to 1B 11 on wire
    frame = adapter.build_datetime_command(2026, 5, 21, 22, 53, 0)
    assert frame[0] == 0x1A
    assert frame[-1] == 0x1D

    # Verify it matches the captured frame exactly
    captured = "1a0120103ca210a1501b110515163500000087ecf6541d"
    assert frame.hex() == captured

    # Captured session 1: 2026-05-21 15:09:00
    # Wire: 1a0120103ca210a1501b1105150f090000004cbc3d971d
    frame2 = adapter.build_datetime_command(2026, 5, 21, 15, 9, 0)
    captured2 = "1a0120103ca210a1501b1105150f090000004cbc3d971d"
    assert frame2.hex() == captured2

