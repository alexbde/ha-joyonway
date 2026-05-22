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


