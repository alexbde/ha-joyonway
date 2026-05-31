#!/usr/bin/env python3
"""
Phase 4 — CRC algorithm identification for command frames.

Tries various CRC-32 algorithms against the captured command frames
to find which one produces the correct checksum bytes (17-20).
"""
from __future__ import annotations

import struct
import zlib

# Captured command frames
COMMANDS = {
    "PUMP_ON":    "1a0120103ca110a10202000000c00056007dd2146b1d",
    "PUMP_HIGH":  "1a0120103ca110a10604000000c0005600fc1221c61d",
    "PUMP_OFF":   "1a0120103ca110a10400000000c0005600735738e91d",
    "LIGHT":      "1a0120103ca110a10000404000c00056003031eeb21d",
    "TEMP_UP":    "1a0120103ca110a10000808000c00057005aa3207f1d",
    "TEMP_DOWN":  "1a0120103ca110a10000808000c0005600dd0ff87e1d",
}


def try_crc32_variants(data: bytes, expected: bytes, label: str):
    """Try various CRC-32 algorithms on data, check if any match expected."""
    results = []

    # Standard CRC-32 (zlib, same as binascii.crc32)
    crc = zlib.crc32(data) & 0xFFFFFFFF
    crc_bytes_be = struct.pack(">I", crc)
    crc_bytes_le = struct.pack("<I", crc)
    if crc_bytes_be == expected:
        results.append(f"CRC-32 (zlib) BIG-ENDIAN")
    if crc_bytes_le == expected:
        results.append(f"CRC-32 (zlib) LITTLE-ENDIAN")

    # CRC-32 inverted
    crc_inv = crc ^ 0xFFFFFFFF
    if struct.pack(">I", crc_inv) == expected:
        results.append("CRC-32 (zlib) inverted BE")
    if struct.pack("<I", crc_inv) == expected:
        results.append("CRC-32 (zlib) inverted LE")

    # CRC-32C (Castagnoli)
    try:
        import crcmod
        crc32c_fn = crcmod.predefined.mkCrcFun('crc-32c')
        crc_c = crc32c_fn(data) & 0xFFFFFFFF
        if struct.pack(">I", crc_c) == expected:
            results.append("CRC-32C BE")
        if struct.pack("<I", crc_c) == expected:
            results.append("CRC-32C LE")
    except ImportError:
        pass

    return results


def crc32_custom(data: bytes, poly: int, init: int = 0xFFFFFFFF, xor_out: int = 0xFFFFFFFF, reflect_in: bool = True, reflect_out: bool = True) -> int:
    """Generic CRC-32 calculator."""
    def reflect(val, width):
        result = 0
        for i in range(width):
            if val & (1 << i):
                result |= 1 << (width - 1 - i)
        return result

    crc = init
    for byte in data:
        if reflect_in:
            byte = reflect(byte, 8)
        crc ^= (byte << 24)
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ poly) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    if reflect_out:
        crc = reflect(crc, 32)
    return crc ^ xor_out


# Known CRC-32 polynomials
POLYS = {
    "CRC-32 (ISO 3309)": 0x04C11DB7,
    "CRC-32C (Castagnoli)": 0x1EDC6F41,
    "CRC-32K (Koopman)": 0x741B8CD7,
    "CRC-32Q": 0x814141AB,
    "CRC-32/AUTOSAR": 0xF4ACFB13,
    "CRC-32/MPEG-2": 0x04C11DB7,
}


def main():
    print("Phase 4 — CRC Algorithm Identification")
    print("=" * 70)

    for name, hex_frame in COMMANDS.items():
        frame = bytes.fromhex(hex_frame)
        expected_crc = frame[17:21]
        print(f"\n{name}: expected CRC = {expected_crc.hex()}")

        # Try different payload ranges
        ranges_to_try = [
            ("bytes[1:17]", frame[1:17]),    # dest through payload (skip start/end)
            ("bytes[2:17]", frame[2:17]),    # skip start+dest
            ("bytes[0:17]", frame[0:17]),    # include start byte
            ("bytes[1:16]", frame[1:16]),    # shorter
            ("bytes[2:16]", frame[2:16]),    # shorter still
            ("bytes[8:17]", frame[8:17]),    # payload only
            ("bytes[8:16]", frame[8:16]),    # command payload only
            ("bytes[2:15]", frame[2:15]),    # header + payload no setpoint
        ]

        for range_name, data in ranges_to_try:
            results = try_crc32_variants(data, expected_crc, name)
            if results:
                print(f"  ✅ MATCH with {range_name}: {', '.join(results)}")

        # Also try with the standard zlib CRC-32 on different ranges
        # and show what we get vs what we expect
        print(f"  Probing zlib.crc32 on different byte ranges:")
        for range_name, data in ranges_to_try:
            crc = zlib.crc32(data) & 0xFFFFFFFF
            crc_le = struct.pack("<I", crc)
            crc_be = struct.pack(">I", crc)
            match_le = "✅" if crc_le == expected_crc else "  "
            match_be = "✅" if crc_be == expected_crc else "  "
            print(f"    {range_name:16s} → LE:{crc_le.hex()} {match_le}  BE:{crc_be.hex()} {match_be}")

    # Try all polynomial variants with different settings
    print("\n\n" + "=" * 70)
    print("EXHAUSTIVE POLYNOMIAL SEARCH")
    print("=" * 70)

    # Use first frame as reference
    ref_hex = list(COMMANDS.values())[0]
    ref_frame = bytes.fromhex(ref_hex)
    ref_crc = ref_frame[17:21]
    ref_crc_int_be = struct.unpack(">I", ref_crc)[0]
    ref_crc_int_le = struct.unpack("<I", ref_crc)[0]

    print(f"\nReference: PUMP_ON, expected CRC = {ref_crc.hex()}")
    print(f"  As BE int: 0x{ref_crc_int_be:08X}")
    print(f"  As LE int: 0x{ref_crc_int_le:08X}")

    # Try the common payload range bytes[1:17] with various polys
    payload = ref_frame[1:17]
    print(f"\nPayload bytes[1:17] = {payload.hex()}")

    for poly_name, poly in POLYS.items():
        for init in [0x00000000, 0xFFFFFFFF]:
            for xor in [0x00000000, 0xFFFFFFFF]:
                for ri in [True, False]:
                    for ro in [True, False]:
                        crc = crc32_custom(payload, poly, init, xor, ri, ro)
                        if crc == ref_crc_int_be or crc == ref_crc_int_le:
                            print(f"  ✅ MATCH! poly={poly_name} init=0x{init:08X} "
                                  f"xor=0x{xor:08X} reflect_in={ri} reflect_out={ro} "
                                  f"→ 0x{crc:08X} ({'BE' if crc == ref_crc_int_be else 'LE'})")

    # Also try bytes[2:17]
    payload2 = ref_frame[2:17]
    print(f"\nPayload bytes[2:17] = {payload2.hex()}")
    for poly_name, poly in POLYS.items():
        for init in [0x00000000, 0xFFFFFFFF]:
            for xor in [0x00000000, 0xFFFFFFFF]:
                for ri in [True, False]:
                    for ro in [True, False]:
                        crc = crc32_custom(payload2, poly, init, xor, ri, ro)
                        if crc == ref_crc_int_be or crc == ref_crc_int_le:
                            print(f"  ✅ MATCH! poly={poly_name} init=0x{init:08X} "
                                  f"xor=0x{xor:08X} reflect_in={ri} reflect_out={ro} "
                                  f"→ 0x{crc:08X} ({'BE' if crc == ref_crc_int_be else 'LE'})")

    # Try the whole frame minus start, end, and CRC
    payload3 = ref_frame[1:17]  # skip 0x1A start, include up to before CRC
    print(f"\nPayload bytes[0:17] (include start) = {ref_frame[0:17].hex()}")
    payload3 = ref_frame[0:17]
    for poly_name, poly in POLYS.items():
        for init in [0x00000000, 0xFFFFFFFF]:
            for xor in [0x00000000, 0xFFFFFFFF]:
                for ri in [True, False]:
                    for ro in [True, False]:
                        crc = crc32_custom(payload3, poly, init, xor, ri, ro)
                        if crc == ref_crc_int_be or crc == ref_crc_int_le:
                            print(f"  ✅ MATCH! poly={poly_name} init=0x{init:08X} "
                                  f"xor=0x{xor:08X} reflect_in={ri} reflect_out={ro} "
                                  f"→ 0x{crc:08X} ({'BE' if crc == ref_crc_int_be else 'LE'})")


if __name__ == "__main__":
    main()

