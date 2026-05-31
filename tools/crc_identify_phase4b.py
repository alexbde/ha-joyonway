#!/usr/bin/env python3
"""
Phase 4 — Extended CRC identification.

Tries more CRC variants including:
- Custom init values
- Different data ranges (unescaped)
- CRC on broadcast frames (known structure)
- Modbus CRC-16
- Sum-based checksums
"""
from __future__ import annotations

import struct
import zlib

# Command frames (no escape sequences present)
COMMANDS = [
    ("PUMP_ON",   bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d")),
    ("PUMP_HIGH", bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d")),
    ("PUMP_OFF",  bytes.fromhex("1a0120103ca110a10400000000c0005600735738e91d")),
    ("LIGHT",     bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d")),
    ("TEMP_UP",   bytes.fromhex("1a0120103ca110a10000808000c00057005aa3207f1d")),
    ("TEMP_DOWN", bytes.fromhex("1a0120103ca110a10000808000c0005600dd0ff87e1d")),
]


def crc32_generic(data: bytes, poly: int, init: int, xor_out: int, ref_in: bool, ref_out: bool) -> int:
    """Compute CRC-32 with arbitrary parameters."""
    def reflect(val, width):
        r = 0
        for i in range(width):
            if val & (1 << i):
                r |= 1 << (width - 1 - i)
        return r

    crc = init
    for byte in data:
        b = reflect(byte, 8) if ref_in else byte
        crc ^= (b << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    if ref_out:
        crc = reflect(crc, 32)
    return (crc ^ xor_out) & 0xFFFFFFFF


def crc16_modbus(data: bytes) -> int:
    """Modbus CRC-16."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def checksum_xor(data: bytes) -> int:
    """Simple XOR checksum."""
    result = 0
    for b in data:
        result ^= b
    return result


def checksum_sum(data: bytes) -> int:
    """Sum of all bytes mod 2^32."""
    return sum(data) & 0xFFFFFFFF


def checksum_sum16(data: bytes) -> int:
    """Sum of 16-bit words."""
    total = 0
    for i in range(0, len(data) - 1, 2):
        total += (data[i] << 8) | data[i + 1]
    if len(data) % 2:
        total += data[-1] << 8
    return total & 0xFFFFFFFF


def main():
    print("Extended CRC/Checksum Identification")
    print("=" * 70)

    # For each command frame, extract the expected CRC and try algorithms
    for name, frame in COMMANDS:
        expected = frame[17:21]
        exp_be = struct.unpack(">I", expected)[0]
        exp_le = struct.unpack("<I", expected)[0]

        # Different data ranges to try
        ranges = {
            "[1:17]": frame[1:17],       # skip start, up to CRC
            "[2:17]": frame[2:17],       # skip start+dest
            "[1:16]": frame[1:16],       # skip start, before last payload byte
            "[0:17]": frame[0:17],       # include start
            "[2:16]": frame[2:16],       # just middle
            "[1:15]": frame[1:15],       # shorter
            "[2:15]": frame[2:15],       # shorter
        }

        found = False
        for rng_name, data in ranges.items():
            # Modbus CRC-16
            crc16 = crc16_modbus(data)
            # Could the 4-byte "CRC" be two 16-bit values?

            # Simple sum
            s = checksum_sum(data)
            if s == exp_be or s == exp_le:
                print(f"{name}: Sum {rng_name} matches! ({'BE' if s==exp_be else 'LE'})")
                found = True

            # Negated sum
            ns = (~s + 1) & 0xFFFFFFFF
            if ns == exp_be or ns == exp_le:
                print(f"{name}: Neg-Sum {rng_name} matches! ({'BE' if ns==exp_be else 'LE'})")
                found = True

        if not found:
            pass  # Will try more below

    # Try reverse-engineering: what's the relationship between payload and CRC?
    print("\n\nByte-level analysis of CRC computation:")
    print("=" * 70)
    print("\nLet's see if CRC = f(payload) where payload = bytes[8:17]")
    print("(the variable part of the frame)")
    print()

    for name, frame in COMMANDS:
        payload = frame[8:17]  # variable bytes
        crc = frame[17:21]
        print(f"  {name:12s}: payload={payload.hex():18s} CRC={crc.hex()}")

    # Check if it might be CRC-32 with the polynomial in reflected form
    print("\n\nTrying reflected polynomial forms:")
    print("=" * 70)

    # Common reflected polys
    reflected_polys = {
        "CRC-32 reflected": 0xEDB88320,
        "CRC-32C reflected": 0x82F63B78,
        "CRC-32K reflected": 0xEB31D82E,
    }

    ref_frame = COMMANDS[0][1]  # PUMP_ON
    ref_crc_be = struct.unpack(">I", ref_frame[17:21])[0]
    ref_crc_le = struct.unpack("<I", ref_frame[17:21])[0]

    for rng_name in ["[1:17]", "[2:17]", "[0:17]"]:
        if rng_name == "[1:17]":
            data = ref_frame[1:17]
        elif rng_name == "[2:17]":
            data = ref_frame[2:17]
        else:
            data = ref_frame[0:17]

        for poly_name, poly in reflected_polys.items():
            for init in [0x00000000, 0xFFFFFFFF]:
                for xor in [0x00000000, 0xFFFFFFFF]:
                    # Direct table-driven CRC with reflected poly
                    crc = init
                    for byte in data:
                        crc = (crc >> 8) ^ _crc_table(poly)[(crc ^ byte) & 0xFF]
                    crc ^= xor
                    crc &= 0xFFFFFFFF
                    if crc == ref_crc_be:
                        print(f"  ✅ {poly_name} {rng_name} init=0x{init:08X} xor=0x{xor:08X} → BE match!")
                        # Verify against all frames
                        _verify_all(poly, init, xor, rng_name)
                    if crc == ref_crc_le:
                        print(f"  ✅ {poly_name} {rng_name} init=0x{init:08X} xor=0x{xor:08X} → LE match!")
                        _verify_all(poly, init, xor, rng_name)

    # One more idea: maybe the CRC is computed on a DIFFERENT interpretation
    # Perhaps it includes address byte combined differently
    # Or maybe there's an initial seed from the address/header
    print("\n\nTrying init = first bytes of frame:")
    for rng_name, start_idx in [("[8:17]", 8), ("[2:17]", 2)]:
        data = ref_frame[start_idx:17]
        # Try using earlier bytes as init
        for init_range in [(1,5), (2,6), (4,8), (1,3), (2,4)]:
            init_bytes = ref_frame[init_range[0]:init_range[1]]
            if len(init_bytes) == 4:
                init_val = struct.unpack(">I", init_bytes)[0]
            elif len(init_bytes) == 2:
                init_val = struct.unpack(">H", init_bytes)[0] * 0x10001
            else:
                continue

            for poly in [0xEDB88320, 0x04C11DB7, 0x82F63B78]:
                crc = init_val
                for byte in data:
                    crc = (crc >> 8) ^ _crc_table(poly)[(crc ^ byte) & 0xFF]
                crc &= 0xFFFFFFFF
                if crc == ref_crc_be or crc == ref_crc_le:
                    endian = "BE" if crc == ref_crc_be else "LE"
                    print(f"  ✅ poly=0x{poly:08X} range={rng_name} init_from=bytes{init_range} → {endian} match!")


_tables: dict[int, list[int]] = {}

def _crc_table(poly: int) -> list[int]:
    if poly not in _tables:
        table = []
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ poly
                else:
                    crc >>= 1
            table.append(crc & 0xFFFFFFFF)
        _tables[poly] = table
    return _tables[poly]


def _verify_all(poly: int, init: int, xor: int, rng_name: str):
    """Verify a CRC match against all command frames."""
    all_ok = True
    for name, frame in COMMANDS:
        if rng_name == "[1:17]":
            data = frame[1:17]
        elif rng_name == "[2:17]":
            data = frame[2:17]
        else:
            data = frame[0:17]

        expected_be = struct.unpack(">I", frame[17:21])[0]
        expected_le = struct.unpack("<I", frame[17:21])[0]

        crc = init
        for byte in data:
            crc = (crc >> 8) ^ _crc_table(poly)[(crc ^ byte) & 0xFF]
        crc = (crc ^ xor) & 0xFFFFFFFF

        ok = crc == expected_be or crc == expected_le
        endian = "BE" if crc == expected_be else ("LE" if crc == expected_le else "NONE")
        status = "✅" if ok else "❌"
        print(f"    {status} {name:12s}: computed=0x{crc:08X} expected_BE=0x{expected_be:08X} expected_LE=0x{expected_le:08X} [{endian}]")
        if not ok:
            all_ok = False
    if all_ok:
        print("    🎉 ALL FRAMES MATCH!")


if __name__ == "__main__":
    main()

