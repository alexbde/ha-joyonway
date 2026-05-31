#!/usr/bin/env python3
"""
Tests for Joyonway RS485 frame protocol functions.

Pure-stdlib tests using unittest — no pip dependencies required.
Run with: python3 -m pytest tests/test_frame_protocol.py -v
     or:  python3 -m unittest tests.test_frame_protocol -v
"""
from __future__ import annotations

import sys
import os
import unittest

# Add tools/ to path so we can import the shared protocol functions
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from frame_parser_38400 import (
    find_frames,
    pseudo_unescape,
    unescape_frame,
    detect_model,
    get_unescape_policy,
    fahrenheit_to_celsius,
    is_broadcast,
    check_escape_positions,
    FRAME_START,
    FRAME_END,
    ESCAPE_BYTE,
)


# ──────────────────────────────────────────────────────────────
# KDy post #74 sample exactly as documented in docs/
# ──────────────────────────────────────────────────────────────

KDY_POST74_RAW_HEX = (
    "1A FF 01 3C D2 B4 FF 08 03 5E 04 06 04 F5 40 00 "
    "68 01 00 12 21 12 3B 14 00 16 00 04 00 43 00 04 "
    "3B 12 00 14 00 00 00 06 4D 00 00 00 00 00 00 00 "
    "00 00 00 00 00 10 05 08 13 1B 11 12 00 00 4E 28 "
    "33 11 1D"
)
KDY_POST74_RAW = bytes.fromhex(KDY_POST74_RAW_HEX.replace(" ", ""))

# Normalized reference used by parser dry-run fixture and length expectations.
KDY_NORMALIZED_RAW_HEX = (
    "1A FF 01 3C D2 B4 FF 08 03 5E 04 06 04 F5 40 00 "
    "68 01 00 12 21 12 3B 14 00 16 00 04 00 43 00 04 "
    "3B 12 00 14 00 00 00 06 4D 00 00 00 00 00 00 00 "
    "00 00 00 00 00 10 05 08 1B 1B 11 12 00 00 4E 28 "
    "33 1D"
)
KDY_NORMALIZED_RAW = bytes.fromhex(KDY_NORMALIZED_RAW_HEX.replace(" ", ""))


class TestFindFrames(unittest.TestCase):
    """Test frame extraction from raw byte streams."""

    def test_single_frame(self):
        stream = bytes([0x1A, 0x01, 0x02, 0x03, 0x1D])
        frames = find_frames(stream)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], stream)

    def test_multiple_frames(self):
        f1 = bytes([0x1A, 0xAA, 0xBB, 0x1D])
        f2 = bytes([0x1A, 0xCC, 0xDD, 0x1D])
        frames = find_frames(f1 + f2)
        self.assertEqual(len(frames), 2)
        self.assertEqual(frames[0], f1)
        self.assertEqual(frames[1], f2)

    def test_junk_before_frame(self):
        """Bytes before first 0x1A should be skipped."""
        junk = bytes([0x00, 0xFF, 0x42])
        frame = bytes([0x1A, 0x01, 0x02, 0x1D])
        frames = find_frames(junk + frame)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], frame)

    def test_partial_frame_at_end(self):
        """Frame without 0x1D end should be skipped."""
        complete = bytes([0x1A, 0x01, 0x1D])
        partial = bytes([0x1A, 0x02, 0x03])
        frames = find_frames(complete + partial)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], complete)

    def test_empty_stream(self):
        self.assertEqual(find_frames(b""), [])

    def test_no_frames(self):
        """Stream with no delimiters returns empty list."""
        self.assertEqual(find_frames(bytes([0x00, 0x01, 0x02])), [])

    def test_kdy_sample_extraction(self):
        """Both documented and normalized KDy samples are valid single frames."""
        frames = find_frames(KDY_POST74_RAW)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], FRAME_START)
        self.assertEqual(frames[0][-1], FRAME_END)

        frames = find_frames(KDY_NORMALIZED_RAW)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], FRAME_START)
        self.assertEqual(frames[0][-1], FRAME_END)

    def test_frames_between_junk(self):
        """Frames with junk bytes between them."""
        f1 = bytes([0x1A, 0x01, 0x1D])
        junk = bytes([0xFF, 0xFE])
        f2 = bytes([0x1A, 0x02, 0x1D])
        frames = find_frames(f1 + junk + f2)
        self.assertEqual(len(frames), 2)

    def test_minimal_frame(self):
        """Smallest possible frame: just start + end."""
        frames = find_frames(bytes([0x1A, 0x1D]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0], bytes([0x1A, 0x1D]))


class TestPseudoUnescape(unittest.TestCase):
    """Test pseudo-escape reversal."""

    def test_unescape_0x1a(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x11])), bytes([0x1A]))

    def test_unescape_0x1b(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x0B])), bytes([0x1B]))

    def test_unescape_0x1c(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x13])), bytes([0x1C]))

    def test_unescape_0x1d(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x14])), bytes([0x1D]))

    def test_unescape_0x1e(self):
        self.assertEqual(pseudo_unescape(bytes([0x1B, 0x15])), bytes([0x1E]))

    def test_no_escapes(self):
        data = bytes([0x01, 0x02, 0x03, 0x04])
        self.assertEqual(pseudo_unescape(data), data)

    def test_consecutive_escapes(self):
        """Two escape sequences back-to-back."""
        data = bytes([0x1B, 0x11, 0x1B, 0x14])
        self.assertEqual(pseudo_unescape(data), bytes([0x1A, 0x1D]))

    def test_trailing_lone_escape(self):
        """Trailing lone 0x1B (malformed) should be preserved as-is."""
        data = bytes([0x01, 0x02, 0x1B])
        self.assertEqual(pseudo_unescape(data), data)

    def test_unknown_escape_suffix(self):
        """0x1B followed by unrecognized suffix should be preserved."""
        data = bytes([0x1B, 0xFF])
        self.assertEqual(pseudo_unescape(data), data)

    def test_mixed_escaped_and_normal(self):
        data = bytes([0x41, 0x1B, 0x11, 0x42, 0x1B, 0x0B, 0x43])
        expected = bytes([0x41, 0x1A, 0x42, 0x1B, 0x43])
        self.assertEqual(pseudo_unescape(data), expected)

    def test_empty(self):
        self.assertEqual(pseudo_unescape(b""), b"")


class TestUnescapeFrame(unittest.TestCase):
    """Test frame-level unescape policies."""

    def test_policy_none(self):
        frame = bytes([0x1A, 0x1B, 0x11, 0x1D])
        self.assertEqual(unescape_frame(frame, "none"), frame)

    def test_policy_full(self):
        """Full unescape: payload between start/end is unescaped."""
        frame = bytes([0x1A, 0x1B, 0x11, 0x1D])
        result = unescape_frame(frame, "full")
        self.assertEqual(result, bytes([0x1A, 0x1A, 0x1D]))

    def test_policy_tail_short_frame(self):
        """Tail policy with frame shorter than 55 bytes — no change."""
        frame = bytes([0x1A] + [0x00] * 10 + [0x1D])
        self.assertEqual(unescape_frame(frame, "tail"), frame)

    def test_policy_tail_long_frame(self):
        """Tail policy unescapes only bytes 55+."""
        payload_head = [0x00] * 54  # bytes 1-54 (after 0x1A start)
        payload_tail = [0x1B, 0x11]  # escape sequence in tail
        frame = bytes([0x1A] + payload_head + payload_tail + [0x1D])
        result = unescape_frame(frame, "tail")
        # Bytes 0-54 unchanged, byte 55+ unescaped
        self.assertEqual(result[:55], frame[:55])
        self.assertEqual(result[55], 0x1A)  # unescaped
        self.assertEqual(result[-1], 0x1D)  # end delimiter preserved


class TestKDyGoldenSample(unittest.TestCase):
    """Test parsing of normalized KDy P25B85 reference broadcast frame."""

    def setUp(self):
        # Apply full unescape (P25B85 policy)
        self.logical = unescape_frame(KDY_NORMALIZED_RAW, "full")

    def test_frame_delimiters(self):
        self.assertEqual(self.logical[0], FRAME_START)
        self.assertEqual(self.logical[-1], FRAME_END)

    def test_broadcast_address(self):
        self.assertEqual(self.logical[1], 0xFF)
        self.assertTrue(is_broadcast(self.logical))

    def test_model_signature(self):
        """byte[8] = 0x03 → P25B85."""
        self.assertEqual(self.logical[8], 0x03)
        self.assertEqual(detect_model(self.logical), "P25B85")

    def test_water_temperature(self):
        """byte[9] = 0x5E = 94°F = 34.4°C."""
        self.assertEqual(self.logical[9], 0x5E)
        self.assertEqual(fahrenheit_to_celsius(0x5E), 34.4)

    def test_heater_state(self):
        """byte[15] = 0x00 → heater off."""
        self.assertEqual(self.logical[15], 0x00)

    def test_setpoint(self):
        """byte[16] = 0x68 = 104°F = 40.0°C."""
        self.assertEqual(self.logical[16], 0x68)
        self.assertEqual(fahrenheit_to_celsius(0x68), 40.0)

    def test_light_off(self):
        """byte[18] = 0x00 → light OFF."""
        self.assertEqual(self.logical[18], 0x00)
        self.assertFalse(bool(self.logical[18] & 0x01))

    def test_uv_flag(self):
        """byte[29] UV flag check (0x20 mask)."""
        self.assertEqual(self.logical[29], 0x43)
        # 0x43 & 0x20 = 0x00 → UV is off (0x43 has bits 0x40 + 0x02 + 0x01)
        self.assertTrue(bool(self.logical[29] & 0x40))
        self.assertFalse(bool(self.logical[29] & 0x20))

    def test_escape_in_raw(self):
        """KDy sample has 0x1B 0x11 escape sequence (should unescape to 0x1A)."""
        escapes = check_escape_positions(KDY_NORMALIZED_RAW)
        self.assertGreater(len(escapes), 0)
        # Find the 0x1B 0x11 → 0x1A escape
        found = any(orig == 0x1A for _, _, orig in escapes)
        self.assertTrue(found, "Expected escape 0x1B 0x11 → 0x1A in KDy sample")

    def test_logical_shorter_than_raw(self):
        """After unescape, logical frame should be shorter (escape sequences collapsed)."""
        self.assertLess(len(self.logical), len(KDY_NORMALIZED_RAW))

    def test_raw_frame_length(self):
        """KDy raw frame is 66 bytes."""
        self.assertEqual(len(KDY_NORMALIZED_RAW), 66)

    def test_logical_frame_length(self):
        """After full unescape, logical frame should be 65 bytes (one 2→1 escape)."""
        # The raw has one escape sequence: 0x1B 0x11 at position 57-58
        self.assertEqual(len(self.logical), 65)


class TestKDyPost74Fixture(unittest.TestCase):
    """Ensure post #74 fixture stays byte-for-byte faithful to docs."""

    def test_post74_raw_length(self):
        self.assertEqual(len(KDY_POST74_RAW), 67)

    def test_post74_full_unescape_length(self):
        logical = unescape_frame(KDY_POST74_RAW, "full")
        self.assertEqual(len(logical), 66)


class TestModelDetection(unittest.TestCase):
    """Test model auto-detection from broadcast header."""

    def test_p25b85(self):
        frame = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])
        self.assertEqual(detect_model(frame), "P25B85")

    def test_p23b32(self):
        frame = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])
        self.assertEqual(detect_model(frame), "P23B32")

    def test_unknown_model(self):
        frame = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x99])
        self.assertIsNone(detect_model(frame))

    def test_short_frame(self):
        self.assertIsNone(detect_model(bytes([0x1A, 0xFF])))


class TestUnescapePolicy(unittest.TestCase):
    """Test model → unescape policy mapping."""

    def test_p25b85_full(self):
        self.assertEqual(get_unescape_policy("P25B85"), "full")

    def test_p23b32_tail(self):
        self.assertEqual(get_unescape_policy("P23B32"), "tail")

    def test_unknown_none(self):
        self.assertEqual(get_unescape_policy(None), "none")


class TestFahrenheitToCelsius(unittest.TestCase):
    """Test temperature conversion."""

    def test_94f(self):
        self.assertEqual(fahrenheit_to_celsius(94), 34.4)

    def test_104f(self):
        self.assertEqual(fahrenheit_to_celsius(104), 40.0)

    def test_zero_invalid(self):
        self.assertIsNone(fahrenheit_to_celsius(0))

    def test_over_200_invalid(self):
        self.assertIsNone(fahrenheit_to_celsius(201))

    def test_32f(self):
        self.assertEqual(fahrenheit_to_celsius(32), 0.0)

    def test_212f(self):
        """212°F is over 200, should be None."""
        self.assertIsNone(fahrenheit_to_celsius(212))

    def test_200f(self):
        """200°F is the boundary — should work."""
        self.assertEqual(fahrenheit_to_celsius(200), 93.3)


class TestIsBroadcast(unittest.TestCase):
    """Test broadcast frame identification."""

    def test_broadcast(self):
        self.assertTrue(is_broadcast(bytes([0x1A, 0xFF, 0x01])))

    def test_not_broadcast(self):
        self.assertFalse(is_broadcast(bytes([0x1A, 0x20, 0x01])))

    def test_too_short(self):
        self.assertFalse(is_broadcast(bytes([0x1A])))

    def test_empty(self):
        self.assertFalse(is_broadcast(b""))


class TestFrameValidation(unittest.TestCase):
    """Test basic frame validation properties."""

    def test_frame_delimiters(self):
        frames = find_frames(bytes([0x1A, 0xAA, 0xBB, 0x1D]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][0], FRAME_START)
        self.assertEqual(frames[0][-1], FRAME_END)

    def test_minimum_length(self):
        """A frame must have at least start + end = 2 bytes."""
        frames = find_frames(bytes([0x1A, 0x1D]))
        self.assertEqual(len(frames), 1)
        self.assertEqual(len(frames[0]), 2)

    def test_multiple_broadcasts_in_stream(self):
        """Simulate a bus cycle with multiple broadcast frames."""
        poll = bytes([0x1A, 0x10, 0x01, 0x1D])
        bcast = bytes([0x1A, 0xFF, 0x01, 0x3C, 0x00, 0x1D])
        stream = poll + poll + bcast + poll + bcast
        frames = find_frames(stream)
        broadcasts = [f for f in frames if is_broadcast(f)]
        self.assertEqual(len(frames), 5)
        self.assertEqual(len(broadcasts), 2)


if __name__ == "__main__":
    unittest.main()

