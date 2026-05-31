"""Shared RS485 frame protocol functions for Joyonway PB55x controllers.

Frame boundary detection, pseudo-unescape, validation.
Can be extracted into a shared package later for multi-model unification.
"""
from __future__ import annotations

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

# Pseudo-escape table: escaped pair suffix → original byte
ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}

# Minimum viable broadcast frame length (start + header + some payload + CRC + end)
MIN_BROADCAST_LEN = 20


def find_frames(stream: bytes) -> list[bytes]:
    """Extract frames delimited by 0x1A ... 0x1D from a raw byte stream.

    Operates on raw (wire) bytes — do NOT unescape before calling this.
    """
    frames: list[bytes] = []
    i = 0
    n = len(stream)
    while i < n:
        if stream[i] == FRAME_START:
            j = i + 1
            while j < n:
                if stream[j] == FRAME_END:
                    frames.append(stream[i : j + 1])
                    i = j + 1
                    break
                j += 1
            else:
                break  # partial frame at end of stream
        else:
            i += 1
    return frames


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

    Checks delimiters and minimum size. Does NOT enforce length byte formula
    (not yet fully understood) to avoid rejecting valid frames.
    """
    if len(frame) < 4:
        return False
    if frame[0] != FRAME_START:
        return False
    if frame[-1] != FRAME_END:
        return False
    return True

