# CRC Analysis — P25B85 Command Frames

This file is the CRC derivation/forensics companion.
Canonical protocol behavior and implementation-facing parameters live in
`docs/protocol.md`.

## SOLVED ✅

The CRC algorithm has been fully cracked and verified against 21 unique
same-session frames (all command types: temp, light, pump, heater, blower,
datetime, filter schedule, heat schedule).

### Final parameters (summary)

| Parameter | Value |
|-----------|-------|
| **Polynomial** | `0x04C11DB7` |
| **Init** | `0x00000000` |
| **XorOut** | `0x552D22C8` |
| **Reflected** | No (MSB-first) |
| **Transform** | 32-bit word byte-swap before CRC |
| **CRC storage** | Little-endian at payload bytes 16–19 |

For current algorithm pseudocode and frame construction, see `docs/protocol.md`.

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
