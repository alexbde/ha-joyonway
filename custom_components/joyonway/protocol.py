"""Shared RS485 frame protocol functions for Joyonway PB55x controllers.

Frame boundary detection, pseudo-unescape, validation, CRC computation.
Can be extracted into a shared package later for multi-model unification.
"""

from __future__ import annotations

import struct

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B
SYNC_FRAME = b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d"

# Pseudo-escape table: escaped pair suffix → original byte
ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}

# Reverse escape table: original byte → escaped pair suffix
ESCAPE_MAP_REV: dict[int, int] = {v: k for k, v in ESCAPE_MAP.items()}

# CRC-32 parameters (cracked from same-session capture analysis)
_CRC_POLY = 0x04C11DB7

# Pre-computed CRC-32 lookup table (non-reflected, MSB-first)
_CRC_TABLE: list[int] = []
for _i in range(256):
    _crc = _i << 24
    for _ in range(8):
        if _crc & 0x80000000:
            _crc = ((_crc << 1) & 0xFFFFFFFF) ^ _CRC_POLY
        else:
            _crc = (_crc << 1) & 0xFFFFFFFF
    _CRC_TABLE.append(_crc)


def find_frames_with_indices(stream: bytes) -> list[tuple[bytes, int]]:
    """Extract frames delimited by 0x1A ... 0x1D and their exclusive end indices from a raw stream.

    Operates on raw (wire) bytes — do NOT unescape before calling this.
    """
    frames: list[tuple[bytes, int]] = []
    i = 0
    n = len(stream)
    while i < n:
        if stream[i] == FRAME_START:
            end_idx = -1
            for j in range(i + 1, n):
                if stream[j] == FRAME_END:
                    end_idx = j
                    break
            if end_idx != -1:
                frames.append((stream[i : end_idx + 1], end_idx + 1))
                i = end_idx + 1
            else:
                break  # partial frame at end of stream
        else:
            i += 1
    return frames


def find_frames(stream: bytes) -> list[bytes]:
    """Extract frames delimited by 0x1A ... 0x1D from a raw byte stream.

    Operates on raw (wire) bytes — do NOT unescape before calling this.
    """
    return [frame for frame, _ in find_frames_with_indices(stream)]


def pseudo_unescape(data: bytes) -> bytes:
    """Reverse pseudo-escape encoding within a byte sequence."""
    result = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == ESCAPE_BYTE and i + 1 < n:
            suffix = data[i + 1]
            if suffix in ESCAPE_MAP:
                result.append(ESCAPE_MAP[suffix])
                i += 2
                continue
        result.append(data[i])
        i += 1
    return bytes(result)


def unescape_frame(frame: bytes, full: bool = True) -> bytes:
    """Apply unescape policy to a raw frame.

    Args:
        frame: Raw frame including start/end delimiters.
        full: If True, unescape entire payload (P25B85 policy).
              If False, unescape only tail bytes 55+ (P23B32 policy).
    """
    if full:
        # Unescape everything between start and end delimiters
        return frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
    else:
        # Tail-only: unescape bytes 55+ (for P23B32 datetime zone)
        if len(frame) > 55:
            return frame[:55] + pseudo_unescape(frame[55:-1]) + frame[-1:]
        return frame


def is_broadcast(frame: bytes) -> bool:
    """Check if a frame is a broadcast (destination 0xFF)."""
    return len(frame) > 1 and frame[1] == 0xFF


def validate_frame(frame: bytes) -> bool:
    """Conservative frame validation.

    Checks delimiters and minimum size. Also validates CRC-32 if the frame
    has sufficient payload.
    """
    if len(frame) < 4:
        return False
    if frame[0] != FRAME_START:
        return False
    if frame[-1] != FRAME_END:
        return False

    # CRC validation for frames with enough payload (minimum 16 bytes payload + 4 bytes CRC)
    unescaped = pseudo_unescape(frame[1:-1])
    if len(unescaped) >= 20:
        payload = unescaped[:-4]
        crc_received = struct.unpack("<I", unescaped[-4:])[0]
        crc_expected = compute_crc(payload)
        if crc_received != crc_expected:
            return False
    return True


def pseudo_escape(data: bytes) -> bytes:
    """Apply pseudo-escape encoding to a byte sequence for wire transmission."""
    result = bytearray()
    for b in data:
        if b in ESCAPE_MAP_REV:
            result.append(ESCAPE_BYTE)
            result.append(ESCAPE_MAP_REV[b])
        else:
            result.append(b)
    return bytes(result)


def _word32_swap(data: bytes) -> bytes:
    """Byte-reverse each 32-bit word (MCU byte ordering for CRC peripheral)."""
    result = bytearray()
    for i in range(0, len(data), 4):
        chunk = data[i : i + 4]
        if len(chunk) < 4:
            chunk = chunk + b"\x00" * (4 - len(chunk))
        result.extend(reversed(chunk))
    return bytes(result)


def compute_crc(payload: bytes) -> int:
    """Compute CRC-32 for a payload of any length (multiple of 4 bytes expected).

    Uses the P25B85 CRC algorithm: standard CRC-32 polynomial (0x04C11DB7),
    non-reflected, with 32-bit word byte-swap preprocessing and init=0xFFFFFFFF.
    """
    msg = _word32_swap(payload)
    crc = 0xFFFFFFFF
    for byte in msg:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc


def build_frame(payload: bytes) -> bytes:
    """Build a complete wire-ready frame from a 16-byte command payload.

    Computes CRC, appends it, applies escape encoding, and wraps with
    start/end delimiters.
    """
    crc = compute_crc(payload)
    crc_bytes = struct.pack("<I", crc)  # little-endian
    inner = payload + crc_bytes
    escaped = pseudo_escape(inner)
    return bytes([FRAME_START]) + escaped + bytes([FRAME_END])
