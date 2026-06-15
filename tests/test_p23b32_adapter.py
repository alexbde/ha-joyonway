"""Pytest coverage for P23B32 adapter behavior."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import types

import pytest

from _loader import load_module

ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = ROOT / "custom_components" / "joyonway"

protocol = load_module("joyonway.protocol", PKG_DIR / "protocol.py")
adapters_base = load_module("joyonway.adapters.base", PKG_DIR / "adapters" / "base.py")
adapters_p25 = load_module("joyonway.adapters.p25", PKG_DIR / "adapters" / "p25.py")

adapters_pkg_mod = types.ModuleType("joyonway.adapters")
adapters_pkg_mod.base = adapters_base
adapters_pkg_mod.p25 = adapters_p25
adapters_pkg_mod.SpaEntityDescription = adapters_base.SpaEntityDescription
sys.modules["joyonway.adapters"] = adapters_pkg_mod
sys.modules["joyonway.adapters.p25"] = adapters_p25

adapters_p23 = load_module("joyonway.adapters.p23", PKG_DIR / "adapters" / "p23.py")

unescape_frame = protocol.unescape_frame
pseudo_unescape = protocol.pseudo_unescape
P23B32Adapter = adapters_p23.P23B32Adapter
P23B32_SIGNATURE = adapters_p23.P23B32_SIGNATURE


# Helper to extract payload
def _frame_payload(frame: bytes, length: int = 16) -> bytes:
    """Extract the unescaped payload from a wire frame."""
    return pseudo_unescape(frame[1:-1])[:length]


# Create a mock broadcast frame for P23B32
MOCK_P23_RAW = bytes(
    [0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02, 0x64, 0x00, 0x00]  # 0-11
    + [0x14, 0x90, 0x5D, 0x00, 0x68, 0x81, 0x00]  # 12-18
    + [0x4A, 0x00, 0x0C, 0x1E, 0x56, 0x0F, 0x10, 0x2D, 0x00]  # 19-27 (heat sched + pad)
    + [
        0x00,
        0x48,
        0x00,
        0x0A,
        0x00,
        0x52,
        0x00,
        0x14,
        0x00,
    ]  # 28-36 (pad + filter sched)
    + [0x00] * 16  # 37-52
    + [26, 6, 10, 21, 30, 0]  # 53-58 (datetime)
    + [0x00] * 4  # 59-62 (trailer/CRC placeholders)
    + [0x1D]  # 63
)


@pytest.fixture
def adapter() -> P23B32Adapter:
    return P23B32Adapter()


@pytest.fixture
def logical_frame() -> bytes:
    # P23 unescapes only tail bytes
    return unescape_frame(MOCK_P23_RAW, unescape_full=False)


def test_adapter_properties(adapter: P23B32Adapter, logical_frame: bytes) -> None:
    assert adapter.model == "P23B32"
    assert adapter.unescape_full_frame is False
    assert adapter.supports_writes is True
    assert logical_frame[: len(P23B32_SIGNATURE)] == P23B32_SIGNATURE


def test_parse_status_core_fields(adapter: P23B32Adapter, logical_frame: bytes) -> None:
    result = adapter.parse_status(logical_frame)
    assert isinstance(result, dict)
    assert result["current_temperature"] == 38
    assert result["setpoint"] == 40
    assert result["status"] == "heating"
    assert result["heater_active"] is True
    assert result["heater_enabled"] is True
    assert result["jets_left"] == "on"
    assert result["jets_right"] == "on"
    assert result["light"] is True
    assert result["blower"] is True
    assert result["ozone_active"] is False
    assert result["ozone_mode"] == "manual"
    assert result["heater_mode"] == "manual"


def test_parse_rejects_wrong_signature(
    adapter: P23B32Adapter, logical_frame: bytes
) -> None:
    modified = bytearray(logical_frame)
    modified[8] = 0x03
    assert adapter.parse_status(bytes(modified)) is None


def test_parse_rejects_too_short_frame(adapter: P23B32Adapter) -> None:
    assert (
        adapter.parse_status(
            bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])
        )
        is None
    )


def test_preheat_circulation_status(
    adapter: P23B32Adapter, logical_frame: bytes
) -> None:
    modified = bytearray(logical_frame)
    modified[14] = 0x50  # HEATER_STANDBY
    modified[17] = 0x80  # MASK_HEATING_CYCLE
    result = adapter.parse_status(bytes(modified))
    assert result["status"] == "circulation"


def test_entity_descriptions(adapter: P23B32Adapter) -> None:
    descs = adapter.entity_descriptions()
    assert descs
    keys = {d.key for d in descs}
    assert "jets" not in keys
    assert "jets_left" in keys
    assert "jets_right" in keys
    assert "current_temperature" in keys
    assert "status" in keys


def test_is_heater_enabled(adapter: P23B32Adapter) -> None:
    assert adapter.is_heater_enabled(None) is None
    assert adapter.is_heater_enabled({"heater_enabled": True}) is True
    assert (
        adapter.is_heater_enabled({"heater_enabled": None, "status": "standby"}) is True
    )
    assert adapter.is_heater_enabled({"heater_enabled": None, "status": "off"}) is False


def test_get_jets_state(adapter: P23B32Adapter) -> None:
    data = {"jets_left": "on", "jets_right": "off"}
    assert adapter.get_jets_state(data, "jets_left") == "on"
    assert adapter.get_jets_state(data, "jets_right") == "off"
    assert adapter.get_jets_state(data, "other") == "off"


def test_parse_datetime(adapter: P23B32Adapter, logical_frame: bytes) -> None:
    result = adapter.parse_status(logical_frame)
    assert isinstance(result["spa_datetime"], datetime)
    assert result["spa_datetime"].year == 2026
    assert result["spa_datetime"].month == 6
    assert result["spa_datetime"].day == 10
    assert result["spa_datetime"].hour == 21
    assert result["spa_datetime"].minute == 30
    assert result["spa_datetime"].second == 0


def test_parse_schedules(adapter: P23B32Adapter, logical_frame: bytes) -> None:
    result = adapter.parse_status(logical_frame)
    assert result["heat_slot1_start"] == (10, 0)
    assert result["heat_slot1_end"] == (12, 30)
    assert result["heat_slot1_enabled"] is True

    # 0x56 is 22 | 0x40 (10 PM)
    assert result["heat_slot2_start"] == (22, 15)
    assert result["heat_slot2_end"] == (16, 45)
    assert result["heat_slot2_enabled"] is True

    assert result["filter_slot1_start"] == (8, 0)
    assert result["filter_slot1_end"] == (10, 0)
    assert result["filter_slot1_enabled"] is True

    assert result["filter_slot2_start"] == (18, 0)
    assert result["filter_slot2_end"] == (20, 0)
    assert result["filter_slot2_enabled"] is True


def test_build_light_command(adapter: P23B32Adapter) -> None:
    on_frame = adapter.build_light_command(on=True)
    p_on = _frame_payload(on_frame, length=17)
    assert p_on[16] == 0x81  # ON marker

    off_frame = adapter.build_light_command(on=False)
    p_off = _frame_payload(off_frame, length=17)
    assert p_off[16] == 0x80  # OFF marker

    # First 16 bytes are identical
    assert p_on[:16] == p_off[:16]


def test_build_jets_command(adapter: P23B32Adapter) -> None:
    # Left Pump ON
    on_left = adapter.build_jets_command("jets_left", "on")
    assert on_left is not None
    p_on_left = _frame_payload(on_left)
    assert p_on_left[7] == 0x06 and p_on_left[8] == 0x04

    # Left Pump OFF
    off_left = adapter.build_jets_command("jets_left", "off")
    assert off_left is not None
    p_off_left = _frame_payload(off_left)
    assert p_off_left[7] == 0x06 and p_off_left[8] == 0x00

    # Right Pump ON
    on_right = adapter.build_jets_command("jets_right", "on")
    assert on_right is not None
    p_on_right = _frame_payload(on_right)
    assert p_on_right[7] == 0x18 and p_on_right[8] == 0x10

    # Right Pump OFF
    off_right = adapter.build_jets_command("jets_right", "off")
    assert off_right is not None
    p_off_right = _frame_payload(off_right)
    assert p_off_right[7] == 0x18 and p_off_right[8] == 0x00

    # Invalid jet_id or target returns None
    assert adapter.build_jets_command("invalid_jet", "on") is None


def test_build_heater_command(adapter: P23B32Adapter) -> None:
    on_frame = adapter.build_heater_command(on=True)
    p_on = _frame_payload(on_frame)
    assert p_on[9] == 0x08 and p_on[10] == 0x18

    off_frame = adapter.build_heater_command(on=False)
    p_off = _frame_payload(off_frame)
    assert p_off[9] == 0x08 and p_off[10] == 0x11


def test_build_blower_command(adapter: P23B32Adapter) -> None:
    on_frame = adapter.build_blower_command(on=True)
    p_on = _frame_payload(on_frame)
    assert p_on[9] == 0x04 and p_on[10] == 0x04

    off_frame = adapter.build_blower_command(on=False)
    p_off = _frame_payload(off_frame)
    assert p_off[9] == 0x04 and p_off[10] == 0x00


def test_build_temp_command(adapter: P23B32Adapter) -> None:
    frame = adapter.build_temp_command(37)
    assert frame is not None
    p = _frame_payload(frame)
    assert p[9] == 0x80 and p[10] == 0x80 and p[14] == 99  # 37°C = 99°F

    assert adapter.build_temp_command(9) is None
    assert adapter.build_temp_command(41) is None


def test_build_unimplemented_mode_commands(adapter: P23B32Adapter) -> None:
    assert adapter.build_ozone_mode_command("auto") == b""
    assert adapter.build_heater_mode_command("auto") == b""


def test_build_ozone_manual_command(adapter: P23B32Adapter) -> None:
    on_frame = adapter.build_ozone_manual_command(on=True)
    p_on = _frame_payload(on_frame)
    assert p_on[9] == 0x01 and p_on[10] == 0x01

    off_frame = adapter.build_ozone_manual_command(on=False)
    p_off = _frame_payload(off_frame)
    assert p_off[9] == 0x01 and p_off[10] == 0x10


def test_build_schedule_command(adapter: P23B32Adapter) -> None:
    frame = adapter.build_schedule_command(
        "heat",
        slot1_start=(10, 0),
        slot1_end=(12, 0),
        slot2_start=(14, 0),
        slot2_end=(16, 0),
        slot1_enabled=True,
        slot2_enabled=False,
        write_mode="time",
    )
    p = _frame_payload(frame)
    assert p[4] == 0xA3  # heat
    assert p[7] == 0x6A  # time write s1 on, s2 off

    with pytest.raises(ValueError):
        adapter.build_schedule_command("invalid", (10, 0), (12, 0), (14, 0), (16, 0))
    with pytest.raises(ValueError):
        adapter.build_schedule_command(
            "heat", (10, 0), (12, 0), (14, 0), (16, 0), write_mode="invalid"
        )


def test_build_datetime_command(adapter: P23B32Adapter) -> None:
    frame = adapter.build_datetime_command(2026, 6, 10, 21, 30, 0, set_date=True)
    p = _frame_payload(frame)
    assert p[4] == 0xA2
    assert p[7] == 0x05  # date + time
    assert p[8] == 26  # 2026-2000
    assert p[9] == 6
    assert p[10] == 10
    assert p[11] == 21
    assert p[12] == 30
    assert p[13] == 0

    frame_time = adapter.build_time_command(21, 30, 0)
    p_time = _frame_payload(frame_time)
    assert p_time[7] == 0x50  # time only

    frame_date = adapter.build_date_command(2026, 6, 10, 21, 30, 0)
    p_date = _frame_payload(frame_date)
    assert p_date[7] == 0x05  # date + time
