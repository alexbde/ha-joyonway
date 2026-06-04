"""Pytest coverage for the analysis tool frame parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from _loader import load_module

ROOT = Path(__file__).resolve().parents[1]
tool_module = load_module(
    "frame_parser_38400", ROOT / "tools" / "frame_parser_38400.py"
)

find_frames = tool_module.find_frames
pseudo_unescape = tool_module.pseudo_unescape
unescape_frame = tool_module.unescape_frame
detect_model = tool_module.detect_model
get_unescape_policy = tool_module.get_unescape_policy
fahrenheit_to_celsius = tool_module.fahrenheit_to_celsius
is_broadcast = tool_module.is_broadcast
check_escape_positions = tool_module.check_escape_positions
annotate_p25b85 = tool_module.annotate_p25b85
FRAME_START = tool_module.FRAME_START
FRAME_END = tool_module.FRAME_END
ESCAPE_BYTE = tool_module.ESCAPE_BYTE


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


@pytest.mark.parametrize(
    ("stream", "count"),
    [
        (bytes([0x1A, 0x01, 0x02, 0x03, 0x1D]), 1),
        (bytes([0x1A, 0xAA, 0x1D, 0x1A, 0xBB, 0x1D]), 2),
        (bytes([0x00, 0xFF, 0x42, 0x1A, 0x01, 0x02, 0x1D]), 1),
        (b"", 0),
        (bytes([0x00, 0x01, 0x02]), 0),
        (bytes([0x1A, 0x1D]), 1),
    ],
)
def test_find_frames(stream: bytes, count: int) -> None:
    assert len(find_frames(stream)) == count


def test_find_frames_partial_frame_is_ignored() -> None:
    complete = bytes([0x1A, 0x01, 0x1D])
    partial = bytes([0x1A, 0x02, 0x03])
    frames = find_frames(complete + partial)
    assert frames == [complete]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (bytes([0x1B, 0x11]), bytes([0x1A])),
        (bytes([0x1B, 0x0B]), bytes([0x1B])),
        (bytes([0x1B, 0x13]), bytes([0x1C])),
        (bytes([0x1B, 0x14]), bytes([0x1D])),
        (bytes([0x1B, 0x15]), bytes([0x1E])),
        (bytes([0x1B, 0x11, 0x1B, 0x14]), bytes([0x1A, 0x1D])),
    ],
)
def test_pseudo_unescape(raw: bytes, expected: bytes) -> None:
    assert pseudo_unescape(raw) == expected


def test_pseudo_unescape_handles_unknown_or_malformed_sequences() -> None:
    assert pseudo_unescape(bytes([0x01, 0x02, 0x1B])) == bytes([0x01, 0x02, 0x1B])
    assert pseudo_unescape(bytes([0x1B, 0xFF])) == bytes([0x1B, 0xFF])


def test_unescape_policy_full() -> None:
    frame = bytes([0x1A, 0x1B, 0x11, 0x1D])
    assert unescape_frame(frame, "full") == bytes([0x1A, 0x1A, 0x1D])


def test_unescape_policy_tail() -> None:
    frame = bytes([0x1A] + [0x00] * 54 + [0x1B, 0x11, 0x1D])
    result = unescape_frame(frame, "tail")
    assert result[:55] == frame[:55]
    assert result[55] == 0x1A
    assert result[-1] == 0x1D


@pytest.fixture
def kdy_logical() -> bytes:
    return unescape_frame(KDY_NORMALIZED_RAW, "full")


def test_kdy_golden_sample(kdy_logical: bytes) -> None:
    assert kdy_logical[0] == FRAME_START
    assert kdy_logical[-1] == FRAME_END
    assert kdy_logical[1] == 0xFF
    assert is_broadcast(kdy_logical)
    assert detect_model(kdy_logical) == "P25B85"
    assert kdy_logical[9] == 0x5E
    assert fahrenheit_to_celsius(0x5E) == 34.4
    assert kdy_logical[16] == 0x68
    assert fahrenheit_to_celsius(0x68) == 40.0
    assert len(kdy_logical) == 65


def test_kdy_annotation_and_escapes(kdy_logical: bytes) -> None:
    escapes = check_escape_positions(KDY_NORMALIZED_RAW)
    assert escapes
    assert any(orig == 0x1A for _, _, orig in escapes)
    byte_28 = next(a for a in annotate_p25b85(kdy_logical) if a["byte"] == 28)
    assert byte_28["name"] == "activity_flag"
    assert "heating_or_uv" in byte_28["decoded"]


def test_kdy_post74_fixture_lengths() -> None:
    assert len(KDY_POST74_RAW) == 67
    assert len(unescape_frame(KDY_POST74_RAW, "full")) == 66


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03]), "P25B85"),
        (bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02]), "P23B32"),
        (bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x99]), None),
        (bytes([0x1A, 0xFF]), None),
    ],
)
def test_model_detection(frame: bytes, expected: str | None) -> None:
    assert detect_model(frame) == expected


def test_unescape_policy_mapping() -> None:
    assert get_unescape_policy("P25B85") == "full"
    assert get_unescape_policy("P23B32") == "tail"
    assert get_unescape_policy(None) == "none"


@pytest.mark.parametrize(
    ("fahrenheit", "expected"),
    [(94, 34.4), (104, 40.0), (32, 0.0), (200, 93.3), (0, None), (201, None)],
)
def test_fahrenheit_to_celsius(fahrenheit: int, expected: float | None) -> None:
    assert fahrenheit_to_celsius(fahrenheit) == expected


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (bytes([0x1A, 0xFF, 0x01]), True),
        (bytes([0x1A, 0x20, 0x01]), False),
        (bytes([0x1A]), False),
        (b"", False),
    ],
)
def test_is_broadcast(frame: bytes, expected: bool) -> None:
    assert is_broadcast(frame) is expected


def test_frame_delimiters_and_stream_mix() -> None:
    stream = (
        bytes([0x1A, 0x10, 0x01, 0x1D])
        + bytes([0x1A, 0x10, 0x01, 0x1D])
        + bytes([0x1A, 0xFF, 0x01, 0x3C, 0x00, 0x1D])
        + bytes([0x1A, 0x10, 0x01, 0x1D])
    )
    frames = find_frames(stream)
    assert frames[0][0] == FRAME_START
    assert frames[0][-1] == FRAME_END
    assert len([f for f in frames if is_broadcast(f)]) == 1
