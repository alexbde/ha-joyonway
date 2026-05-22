# CRC Analysis — P25B85 Command Frames

## SOLVED ✅

The CRC algorithm has been fully cracked and verified against 21 unique
same-session frames (all command types: temp, light, pump, heater, blower,
datetime, filter schedule, heat schedule).

### Algorithm

| Parameter | Value |
|-----------|-------|
| **Polynomial** | `0x04C11DB7` (standard CRC-32 / Ethernet) |
| **Init** | `0x00000000` |
| **XorOut** | `0x552D22C8` |
| **Reflected** | No (MSB-first, non-reflected) |
| **Message** | inner[0:16] (16 bytes between 0x1A/0x1D, unescaped) |
| **Transform** | Each 32-bit word byte-reversed before CRC |
| **CRC storage** | Little-endian at inner[16:20] |

### How to compute

```python
import struct

POLY = 0x04C11DB7
INIT = 0x00000000
XOR_OUT = 0x552D22C8

def word32_swap(data: bytes) -> bytes:
    """Byte-reverse each 32-bit word."""
    result = bytearray()
    for i in range(0, len(data), 4):
        result.extend(reversed(data[i:i+4]))
    return bytes(result)

def crc32_p25b85(payload_16: bytes) -> int:
    """Compute CRC-32 for a 16-byte P25B85 command payload."""
    msg = word32_swap(payload_16)
    crc = INIT
    for byte in msg:
        crc ^= byte << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) & 0xFFFFFFFF) ^ POLY
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc ^ XOR_OUT

def build_command_frame(payload_16: bytes) -> bytes:
    """Build a complete wire frame from 16-byte payload."""
    crc = crc32_p25b85(payload_16)
    crc_bytes = struct.pack('<I', crc)  # little-endian
    inner = payload_16 + crc_bytes
    # Escape and frame
    escaped = escape(inner)
    return b'\\x1a' + escaped + b'\\x1d'
```

### Why the XorOut is non-zero

XorOut = `0x552D22C8` is equivalent to CRC-32/MPEG-2 (init=0xFFFFFFFF,
xor_out=0x00000000) with a different effective init. The constant header
bytes `01 20 10 3c a1 10 a1 00` (which never change across frames)
contribute a fixed value that gets absorbed into the effective XorOut.

### Why standard GCD failed initially

The constraint search revealed the message is **32-bit word byte-swapped**
before CRC computation. This is characteristic of an ARM Cortex-M MCU
(big-endian addressing) with a hardware CRC peripheral that expects
little-endian word input. The PB554 touchpad likely uses such an MCU.

This byte swap caused the GF(2) polynomial GCD approach to fail because
the bit-position relationships between byte[9] and byte[10] (which are in
the SAME 32-bit word and thus swap positions) don't match standard
sequential byte ordering assumptions.

---

## Historical Analysis (for reference)

### 1. Linearity proof

For frames differing only in one byte position, the CRC XOR depends only
on the byte XOR value and position. Tested with 24 same-session frames.

Linearity check: **PASSED**

### 2. Per-bit CRC contributions

| Byte (inner) | Bit | CRC_LE contribution |
|------|-----|---------------------|
| 10 | 2 | 0x399CBDF3 |
| 10 | 3 | 0x73397BE6 |
| 14 | 1 | 0x03B1590E |
| 14 | 2 | 0x0762B21C |
| 14 | 3 | 0x0EC56438 |

Doubling pattern: C[b+1] = C[b] << 1 (confirmed at all positions).

### 3. Brute-force polynomial extraction

Exhaustive search of all 2^32 CRC-32 polynomials with shift-register
constraints at three byte positions (inner[9], inner[10], inner[14])
yielded exactly **one hit**: P = 0x04C11DB7.

Parameters: FWD D=33 (byte[14]→byte[10]), C2=rev (byte[9]→byte[10]).
Runtime: 409 seconds (6.8 minutes), 4.29 billion candidates tested.

### 4. Full verification

21/21 unique frames verified with CRC-32 (normal, init=0, xor_out=0x552D22C8)
applied to word32-swapped payload. Covers all command types: temperature,
light, pump, heater, blower, datetime set, filter schedule, heat schedule.
