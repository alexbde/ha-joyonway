#!/usr/bin/env python3
"""Tests for the Joyonway P25B85 adapter and integration protocol module.

Run with: python3 -m pytest tests/test_p25b85_adapter.py -v
"""
from __future__ import annotations

import sys
import os
import unittest

# Add custom_components/joyonway_p25b85 to path directly (avoids importing __init__.py
# which requires homeassistant). We import protocol and adapters as standalone modules.
_pkg_dir = os.path.join(os.path.dirname(__file__), "..", "custom_components", "joyonway_p25b85")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components"))

# Import protocol module directly (no HA dependency)
import importlib.util

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# Load modules without triggering __init__.py (which needs homeassistant)
protocol = _load_module("joyonway_p25b85.protocol", os.path.join(_pkg_dir, "protocol.py"))
adapters_base = _load_module("joyonway_p25b85.adapters.base", os.path.join(_pkg_dir, "adapters", "base.py"))
sys.modules["joyonway_p25b85.adapters"] = type(sys)("joyonway_p25b85.adapters")
sys.modules["joyonway_p25b85.adapters"].base = adapters_base
sys.modules["joyonway_p25b85.adapters"].SpaEntityDescription = adapters_base.SpaEntityDescription
adapters_p25b85 = _load_module("joyonway_p25b85.adapters.p25b85", os.path.join(_pkg_dir, "adapters", "p25b85.py"))

# Now load the adapters __init__ for registry
_adapters_init_path = os.path.join(_pkg_dir, "adapters", "__init__.py")
adapters_pkg = _load_module("joyonway_p25b85.adapters_init", _adapters_init_path)

find_frames = protocol.find_frames
pseudo_unescape = protocol.pseudo_unescape
unescape_frame = protocol.unescape_frame
is_broadcast = protocol.is_broadcast
validate_frame = protocol.validate_frame
FRAME_START = protocol.FRAME_START
FRAME_END = protocol.FRAME_END

P25B85Adapter = adapters_p25b85.P25B85Adapter
P25B85_SIGNATURE = adapters_p25b85.P25B85_SIGNATURE
IDX_WATER_TEMP = adapters_p25b85.IDX_WATER_TEMP
IDX_SETPOINT = adapters_p25b85.IDX_SETPOINT
IDX_HEATER_STATE = adapters_p25b85.IDX_HEATER_STATE
IDX_LIGHT_FLAGS = adapters_p25b85.IDX_LIGHT_FLAGS
IDX_PUMP_BYTE = adapters_p25b85.IDX_PUMP_BYTE
IDX_UV_FLAG = adapters_p25b85.IDX_UV_FLAG
HEATER_OFF = adapters_p25b85.HEATER_OFF
HEATER_HEATING = adapters_p25b85.HEATER_HEATING
HEATER_CIRCULATION = adapters_p25b85.HEATER_CIRCULATION
HEATER_COOLDOWN = adapters_p25b85.HEATER_COOLDOWN
HEATER_UV_OZONE = adapters_p25b85.HEATER_UV_OZONE
_fahrenheit_to_celsius = adapters_p25b85._fahrenheit_to_celsius

get_adapter = adapters_pkg.get_adapter
ADAPTERS = adapters_pkg.ADAPTERS


# KDy reference frame (normalized: 0x13 → 0x1B at byte 55, 0x11 → end marker correction)
# This is the wire frame as it would appear on TCP
KDY_RAW_HEX = (
    "1A FF 01 3C D2 B4 FF 08 03 5E 04 06 04 F5 40 00 "
    "68 01 00 12 21 12 3B 14 00 16 00 04 00 43 00 04 "
    "3B 12 00 14 00 00 00 06 4D 00 00 00 00 00 00 00 "
    "00 00 00 00 00 10 05 08 1B 1B 11 12 00 00 4E 28 "
    "33 1D"
)
KDY_RAW = bytes.fromhex(KDY_RAW_HEX.replace(" ", ""))


class TestProtocolModule(unittest.TestCase):
    """Test the protocol.py module (shared frame handling)."""

    def test_find_frames_single(self):
        stream = bytes([0x1A, 0x01, 0x02, 0x1D])
        frames = find_frames(stream)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], stream)

    def test_find_frames_multiple(self):
        f1 = bytes([0x1A, 0xAA, 0x1D])
        f2 = bytes([0x1A, 0xBB, 0x1D])
        self.assertEqual(len(find_frames(f1 + f2)), 2)

    def test_find_frames_with_junk(self):
        junk = bytes([0xFF, 0xFE])
        frame = bytes([0x1A, 0x01, 0x1D])
        self.assertEqual(len(find_frames(junk + frame)), 1)

    def test_pseudo_unescape_all_sequences(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x11])), bytes([0x1A]))
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x0B])), bytes([0x1B]))
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x13])), bytes([0x1C]))
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x14])), bytes([0x1D]))
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x15])), bytes([0x1E]))

    def test_unescape_frame_full(self):
        frame = bytes([0x1A, 0x1B, 0x11, 0x1D])
        result = unescape_frame(frame, full=True)
        self.assertEqual(result, bytes([0x1A, 0x1A, 0x1D]))

    def test_unescape_frame_preserves_delimiters(self):
        result = unescape_frame(KDY_RAW, full=True)
        self.assertEqual(result[0], FRAME_START)
        self.assertEqual(result[-1], FRAME_END)

    def test_is_broadcast(self):
        self.assertTrue(is_broadcast(bytes([0x1A, 0xFF, 0x01])))
        self.assertFalse(is_broadcast(bytes([0x1A, 0x20, 0x01])))

    def test_validate_frame(self):
        self.assertTrue(validate_frame(bytes([0x1A, 0x01, 0x02, 0x1D])))
        self.assertFalse(validate_frame(bytes([0x1A, 0x1D])))  # too short (< 4)
        self.assertFalse(validate_frame(bytes([0xFF, 0x01, 0x02, 0x1D])))  # wrong start

    def test_kdy_frame_extraction(self):
        frames = find_frames(KDY_RAW)
        self.assertEqual(len(frames), 1)
        self.assertTrue(is_broadcast(frames[0]))
        self.assertTrue(validate_frame(frames[0]))


class TestP25B85Adapter(unittest.TestCase):
    """Test the P25B85 adapter parse_status against KDy golden sample."""

    def setUp(self):
        self.adapter = P25B85Adapter()
        # Apply full unescape (P25B85 policy)
        self.logical = unescape_frame(KDY_RAW, full=True)

    def test_adapter_properties(self):
        self.assertEqual(self.adapter.model, "P25B85")
        self.assertTrue(self.adapter.unescape_full_frame)
        self.assertFalse(self.adapter.supports_writes)

    def test_signature_matches(self):
        self.assertEqual(
            self.logical[: len(P25B85_SIGNATURE)], P25B85_SIGNATURE
        )

    def test_parse_returns_dict(self):
        result = self.adapter.parse_status(self.logical)
        self.assertIsNotNone(result)
        self.assertIsInstance(result, dict)

    def test_water_temperature(self):
        result = self.adapter.parse_status(self.logical)
        # byte[9] = 0x5E = 94°F = 34.4°C
        self.assertEqual(result["water_temperature"], 34.4)

    def test_setpoint(self):
        result = self.adapter.parse_status(self.logical)
        # byte[16] = 0x68 = 104°F = 40.0°C
        self.assertEqual(result["setpoint"], 40.0)

    def test_heater_state_off(self):
        result = self.adapter.parse_status(self.logical)
        # byte[15] = 0x00 → off
        self.assertEqual(result["heater_state"], "off")
        self.assertFalse(result["heater_active"])

    def test_light_off(self):
        result = self.adapter.parse_status(self.logical)
        # byte[18] = 0x00 → light off
        self.assertFalse(result["light"])

    def test_uv_off(self):
        result = self.adapter.parse_status(self.logical)
        # byte[29] = 0x43, 0x43 & 0x20 = 0 → UV off
        # byte[15] = 0x00, not 0xC1 → UV off
        self.assertFalse(result["uv_lamp"])

    def test_pump_values(self):
        result = self.adapter.parse_status(self.logical)
        # byte[12] = 0x04 → pump_high should be True (0x04 & 0x04)
        # byte[12] = 0x04 → pump_low should be False (0x04 & 0x02 = 0)
        self.assertTrue(result["pump_high"])
        self.assertFalse(result["pump_low"])

    def test_raw_diagnostics_present(self):
        result = self.adapter.parse_status(self.logical)
        self.assertIn("raw_pump_byte", result)
        self.assertIn("raw_heater_byte", result)
        self.assertEqual(result["raw_water_temp_f"], 0x5E)
        self.assertEqual(result["raw_setpoint_f"], 0x68)

    def test_rejects_wrong_signature(self):
        # Change byte[8] from 0x03 to 0x02 (P23B32 signature)
        modified = bytearray(self.logical)
        modified[8] = 0x02
        result = self.adapter.parse_status(bytes(modified))
        self.assertIsNone(result)

    def test_rejects_short_frame(self):
        result = self.adapter.parse_status(bytes([0x1A, 0xFF, 0x01, 0x1D]))
        self.assertIsNone(result)

    def test_entity_descriptions(self):
        descs = self.adapter.entity_descriptions()
        self.assertGreater(len(descs), 0)

        # Check we have sensors and binary_sensors
        platforms = {d.platform for d in descs}
        self.assertIn("sensor", platforms)
        self.assertIn("binary_sensor", platforms)

        # Check key entities exist
        keys = {d.key for d in descs}
        self.assertIn("water_temperature", keys)
        self.assertIn("setpoint", keys)
        self.assertIn("pump_low", keys)
        self.assertIn("pump_high", keys)
        self.assertIn("light", keys)
        self.assertIn("heater_active", keys)
        self.assertIn("uv_lamp", keys)
        self.assertIn("heater_state", keys)


class TestP25B85HeaterStates(unittest.TestCase):
    """Test all heater state byte values."""

    def setUp(self):
        self.adapter = P25B85Adapter()
        self.base_logical = unescape_frame(KDY_RAW, full=True)

    def _with_heater_byte(self, value: int) -> bytes:
        modified = bytearray(self.base_logical)
        modified[IDX_HEATER_STATE] = value
        return bytes(modified)

    def test_heater_off(self):
        result = self.adapter.parse_status(self._with_heater_byte(HEATER_OFF))
        self.assertEqual(result["heater_state"], "off")
        self.assertFalse(result["heater_active"])

    def test_heater_circulation(self):
        result = self.adapter.parse_status(self._with_heater_byte(HEATER_CIRCULATION))
        self.assertEqual(result["heater_state"], "circulation")
        self.assertFalse(result["heater_active"])

    def test_heater_heating(self):
        result = self.adapter.parse_status(self._with_heater_byte(HEATER_HEATING))
        self.assertEqual(result["heater_state"], "heating")
        self.assertTrue(result["heater_active"])

    def test_heater_cooldown(self):
        result = self.adapter.parse_status(self._with_heater_byte(HEATER_COOLDOWN))
        self.assertEqual(result["heater_state"], "cooldown")
        self.assertFalse(result["heater_active"])

    def test_heater_uv_ozone(self):
        result = self.adapter.parse_status(self._with_heater_byte(HEATER_UV_OZONE))
        self.assertEqual(result["heater_state"], "uv_ozone")
        self.assertFalse(result["heater_active"])
        self.assertTrue(result["uv_lamp"])

    def test_heater_unknown(self):
        result = self.adapter.parse_status(self._with_heater_byte(0x99))
        self.assertEqual(result["heater_state"], "unknown")


class TestAdapterRegistry(unittest.TestCase):
    """Test the adapter registry."""

    def test_get_p25b85(self):
        adapter = get_adapter("P25B85")
        self.assertEqual(adapter.model, "P25B85")

    def test_unknown_model_raises(self):
        with self.assertRaises(ValueError):
            get_adapter("UNKNOWN")

    def test_registry_contains_p25b85(self):
        self.assertIn("P25B85", ADAPTERS)


class TestFahrenheitConversion(unittest.TestCase):
    """Test temperature conversion edge cases."""

    def test_normal_values(self):
        self.assertEqual(_fahrenheit_to_celsius(94), 34.4)
        self.assertEqual(_fahrenheit_to_celsius(104), 40.0)
        self.assertEqual(_fahrenheit_to_celsius(32), 0.0)

    def test_zero_invalid(self):
        self.assertIsNone(_fahrenheit_to_celsius(0))

    def test_over_200_invalid(self):
        self.assertIsNone(_fahrenheit_to_celsius(201))
        self.assertIsNone(_fahrenheit_to_celsius(255))

    def test_boundary_200(self):
        self.assertIsNotNone(_fahrenheit_to_celsius(200))


if __name__ == "__main__":
    unittest.main()


