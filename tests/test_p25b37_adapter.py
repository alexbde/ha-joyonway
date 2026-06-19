"""Pytest coverage for the P25B37 model adapter."""

from __future__ import annotations

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

pseudo_unescape = protocol.pseudo_unescape
unescape_frame = protocol.unescape_frame

P25B37Adapter = adapters_p25.P25B37Adapter
P25_SIGNATURE = adapters_p25.P25_SIGNATURE
IDX_OZONE_MODE = adapters_p25.IDX_OZONE_MODE
MASK_BLOWER_CONFIG = adapters_p25.MASK_BLOWER_CONFIG

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
def b37_adapter() -> P25B37Adapter:
    return P25B37Adapter()


@pytest.fixture
def logical_frame() -> bytes:
    return unescape_frame(KDY_RAW, unescape_full=True)


def _frame_payload(frame: bytes) -> bytes:
    return pseudo_unescape(frame[1:-1])[:16]


def test_p25b37_adapter_properties(b37_adapter: P25B37Adapter) -> None:
    assert b37_adapter.model == "P25B37"
    assert b37_adapter._context_byte == 0x40
    assert b37_adapter.unescape_full_frame is True
    assert b37_adapter.supports_writes is True
    assert b37_adapter.has_blower is False


def test_p25b37_build_light_command(b37_adapter: P25B37Adapter) -> None:
    # ON command -> tail_byte = 0x81
    frame_on = b37_adapter.build_light_command(on=True)
    p_on = _frame_payload(frame_on)
    assert p_on[9] == 0x40  # btn_group
    assert p_on[10] == 0x40  # btn_action
    assert p_on[12] == 0x40  # context
    assert p_on[15] == 0x81  # tail_byte for ON

    # OFF command -> tail_byte = 0x80
    frame_off = b37_adapter.build_light_command(on=False)
    p_off = _frame_payload(frame_off)
    assert p_off[9] == 0x40
    assert p_off[10] == 0x40
    assert p_off[12] == 0x40
    assert p_off[15] == 0x80  # tail_byte for OFF

    # Color command -> tail_byte = 0x83 (green)
    frame_color = b37_adapter.build_light_command(on=True, color="green")
    p_color = _frame_payload(frame_color)
    assert p_color[9] == 0x40
    assert p_color[10] == 0x40
    assert p_color[12] == 0x40
    assert p_color[15] == 0x83

    # Test invalid color raises
    with pytest.raises(ValueError):
        b37_adapter.build_light_command(on=True, color="invalid_color")


def test_p25b37_build_jets_command(b37_adapter: P25B37Adapter) -> None:
    frame = b37_adapter.build_jets_command("jets", "low")
    assert frame is not None
    p = _frame_payload(frame)
    assert p[7] == 0x02 and p[8] == 0x02  # low speed transition
    assert p[12] == 0x40  # context byte matches P25B37 default


def test_p25b37_parse_status(b37_adapter: P25B37Adapter, logical_frame: bytes) -> None:
    result = b37_adapter.parse_status(logical_frame)
    assert isinstance(result, dict)
    assert result["current_temperature"] == 34
    assert result["setpoint"] == 40
    assert result["blower_present"] is False


def test_p25b37_parse_status_blower_present(
    b37_adapter: P25B37Adapter, logical_frame: bytes
) -> None:
    # Set the blower configuration bit on byte 13
    modified = bytearray(logical_frame)
    modified[IDX_OZONE_MODE] |= MASK_BLOWER_CONFIG
    result = b37_adapter.parse_status(bytes(modified))
    assert isinstance(result, dict)
    assert result["blower_present"] is True


def test_adapter_registry() -> None:
    assert "P25B37" in ADAPTERS
    assert get_adapter("P25B37").model == "P25B37"
