#!/usr/bin/env python3
"""
Phase 4 — CRC reverse-engineering using algebraic approach.

Uses the 'reveng' method: XOR of CRCs when messages differ by known bits
reveals polynomial properties. Also tries CRC on unescaped variants and
broadcast frames.
"""
from __future__ import annotations

import struct
import zlib
import os

FRAME_START = 0x1A
FRAME_END = 0x1D

# Command frames
COMMANDS = [
    ("PUMP_ON",   bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d")),
    ("PUMP_HIGH", bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d")),
    ("PUMP_OFF",  bytes.fromhex("1a0120103ca110a10400000000c0005600735738e91d")),
    ("LIGHT",     bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d")),
    ("TEMP_UP",   bytes.fromhex("1a0120103ca110a10000808000c00057005aa3207f1d")),
    ("TEMP_DOWN", bytes.fromhex("1a0120103ca110a10000808000c0005600dd0ff87e1d")),
]

ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

def pseudo_unescape(data: bytes) -> bytes:
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x1B and i + 1 < len(data) and data[i+1] in ESCAPE_MAP:
            result.append(ESCAPE_MAP[data[i+1]])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)


def extract_broadcast_frames(data: bytes) -> list[bytes]:
    """Extract broadcast frames from raw capture data."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frame = data[i:j+1]
                    if len(frame) > 1 and frame[1] == 0xFF:
                        frames.append(frame)
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def _crc_table(poly: int) -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ poly
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFF)
    return table


def crc32_reflected(data: bytes, poly: int, init: int = 0xFFFFFFFF, xor_out: int = 0xFFFFFFFF) -> int:
    """Standard reflected CRC-32 (like zlib but with custom poly)."""
    table = _crc_table(poly)
    crc = init
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return (crc ^ xor_out) & 0xFFFFFFFF


def main():
    print("CRC Reverse Engineering — Algebraic Approach")
    print("=" * 70)

    # TEMP_UP and TEMP_DOWN differ only at byte offset 15 (in full frame), which is
    # offset 14 in the data range bytes[1:17]
    temp_up = COMMANDS[4][1]
    temp_down = COMMANDS[5][1]

    data_up = temp_up[1:17]
    data_down = temp_down[1:17]
    crc_up = struct.unpack(">I", temp_up[17:21])[0]
    crc_down = struct.unpack(">I", temp_down[17:21])[0]
    crc_up_le = struct.unpack("<I", temp_up[17:21])[0]
    crc_down_le = struct.unpack("<I", temp_down[17:21])[0]

    xor_crc_be = crc_up ^ crc_down
    xor_crc_le = crc_up_le ^ crc_down_le

    print(f"TEMP_UP   data[1:17] = {data_up.hex()}")
    print(f"TEMP_DOWN data[1:17] = {data_down.hex()}")
    print(f"Data XOR position: byte 14 (0x57 ^ 0x56 = 0x01, bit 0)")
    print(f"CRC XOR (BE): 0x{xor_crc_be:08X}")
    print(f"CRC XOR (LE): 0x{xor_crc_le:08X}")
    print()

    # For a reflected CRC-32: CRC(A) ^ CRC(B) = CRC(A^B) when init is the same
    # A^B = 16 bytes of zeros except byte[14] = 0x01
    # So xor_crc = the CRC of that specific difference pattern
    diff_msg = bytes(14) + b'\x01' + bytes(1)  # 16 bytes, only byte 14 = 0x01
    print(f"Difference message: {diff_msg.hex()}")
    print()

    # Try known polynomials and see if CRC of diff_msg matches xor_crc
    polys = {
        "CRC-32": 0xEDB88320,
        "CRC-32C": 0x82F63B78,
        "CRC-32K": 0xEB31D82E,
        "CRC-32K2": 0x992C1A4C,
        "CRC-32/AUTOSAR": 0xC8DF352F,
    }

    # For reflected CRC: CRC(A^B) with init=0 and xor_out=0 gives the "signature"
    # of the bit pattern
    for poly_name, poly in polys.items():
        sig = crc32_reflected(diff_msg, poly, init=0, xor_out=0)
        print(f"  {poly_name:20s} sig(diff) = 0x{sig:08X}  "
              f"{'✅ BE!' if sig == xor_crc_be else ''}"
              f"{'✅ LE!' if sig == xor_crc_le else ''}")

    # If no match, try non-reflected form
    print("\nNon-reflected approach:")
    for poly_name_r, poly_r in polys.items():
        # Unreflect the polynomial
        poly_nr = 0
        for i in range(32):
            if poly_r & (1 << i):
                poly_nr |= 1 << (31 - i)
        # Non-reflected CRC
        crc = 0
        for byte in diff_msg:
            crc ^= (byte << 24)
            for _ in range(8):
                if crc & 0x80000000:
                    crc = ((crc << 1) ^ poly_nr) & 0xFFFFFFFF
                else:
                    crc = (crc << 1) & 0xFFFFFFFF
        print(f"  {poly_name_r:20s} (NR poly=0x{poly_nr:08X}) sig = 0x{crc:08X}  "
              f"{'✅ BE!' if crc == xor_crc_be else ''}"
              f"{'✅ LE!' if crc == xor_crc_le else ''}")

    # Let's also try CRC of diff_msg with different lengths
    print("\n\nTrying different data ranges for diff message:")
    # Maybe CRC covers bytes[2:17] (15 bytes) — diff at offset 13
    for start_offset in range(5):
        data_len = 17 - (1 + start_offset)
        diff_pos = 14 - start_offset
        if diff_pos < 0 or diff_pos >= data_len:
            continue
        diff_msg2 = bytes(diff_pos) + b'\x01' + bytes(data_len - diff_pos - 1)
        print(f"\n  Range bytes[{1+start_offset}:17] ({data_len} bytes), diff at pos {diff_pos}:")
        for poly_name, poly in polys.items():
            sig = crc32_reflected(diff_msg2, poly, init=0, xor_out=0)
            print(f"    {poly_name:20s} sig = 0x{sig:08X}  "
                  f"{'✅ BE!' if sig == xor_crc_be else ''}"
                  f"{'✅ LE!' if sig == xor_crc_le else ''}")

    # Brute force: try ALL 32-bit polynomials? No, that's 4 billion.
    # But we can try to solve for the polynomial algebraically.
    #
    # For a standard CRC-32 (reflected), the signature for a single bit flip
    # at position p in a message of length L is determined by:
    #   sig = poly_power(8*(L-1-byte_pos) + bit_pos)
    # where poly_power is repeated multiplication by x mod polynomial.
    #
    # We know: diff is bit 0 of byte 14 in a 16-byte message.
    # Position from end: 8*(16-1-14) + 0 = 8 bits from end of message.
    # So sig = x^8 mod poly (for reflected CRC with init=0, xor=0).
    # For standard CRC-32: x^8 = 0x00000100... wait that's too simple.
    # Actually for reflected CRC, the first byte processed accumulates
    # through L-1 more bytes, so it goes through 8*(L-1) = 120 shifts.
    # The diff at byte[14] goes through 8*(16-1-14) = 8 more CRC iterations.
    #
    # Let's just verify: with standard CRC-32 poly 0xEDB88320:
    # Process 0x01 followed by 1 zero byte:
    test_data = b'\x00' * 14 + b'\x01' + b'\x00'
    sig_std = crc32_reflected(test_data, 0xEDB88320, init=0, xor_out=0)
    print(f"\n\nVerification: std CRC-32 on 16-byte msg with bit0 at byte14:")
    print(f"  Signature = 0x{sig_std:08X}")
    print(f"  Expected  = 0x{xor_crc_be:08X} (BE) or 0x{xor_crc_le:08X} (LE)")

    # Now let's try using reveng's approach with broadcast frames too
    print("\n\n" + "=" * 70)
    print("BROADCAST FRAME CRC ANALYSIS")
    print("=" * 70)

    captures_dir = os.path.join(os.path.dirname(__file__), "..", "captures_phase4")
    baseline_file = os.path.join(captures_dir, "00_cmd_pump_on_baseline.bin")

    if os.path.exists(baseline_file):
        with open(baseline_file, "rb") as f:
            raw_data = f.read()

        bc_frames = extract_broadcast_frames(raw_data)
        print(f"\nFound {len(bc_frames)} broadcast frames in baseline capture")

        if bc_frames:
            # Unescape and analyze first few broadcast frames
            print("\nFirst 5 broadcast frames (raw → unescaped):")
            for i, frame in enumerate(bc_frames[:5]):
                unesc = bytes([frame[0]]) + pseudo_unescape(frame[1:-1]) + bytes([frame[-1]])
                print(f"  [{i}] raw={len(frame)}B unesc={len(unesc)}B")
                print(f"      raw: {frame.hex()}")
                print(f"      une: {unesc.hex()}")
                # Last 4 bytes before frame end = CRC?
                raw_crc = frame[-5:-1]
                une_crc = unesc[-5:-1]
                print(f"      raw CRC (last 4 before 0x1D): {raw_crc.hex()}")
                print(f"      une CRC (last 4 before 0x1D): {une_crc.hex()}")
                print()

            # Check if broadcast CRCs match zlib on various ranges
            print("Testing zlib CRC-32 on unescaped broadcast frames:")
            for i, frame in enumerate(bc_frames[:3]):
                unesc = bytes([frame[0]]) + pseudo_unescape(frame[1:-1]) + bytes([frame[-1]])
                crc_field = unesc[-5:-1]
                exp_be = struct.unpack(">I", crc_field)[0]
                exp_le = struct.unpack("<I", crc_field)[0]

                # Try different ranges
                for desc, data in [
                    ("[1:-5]", unesc[1:-5]),
                    ("[2:-5]", unesc[2:-5]),
                    ("[0:-5]", unesc[0:-5]),
                    ("[1:-3]", unesc[1:-3]),  # maybe CRC is only 2 bytes?
                ]:
                    crc = zlib.crc32(data) & 0xFFFFFFFF
                    match = ""
                    if crc == exp_be:
                        match = "✅ BE!"
                    elif crc == exp_le:
                        match = "✅ LE!"
                    if match:
                        print(f"  Frame {i} {desc}: {match}")

            # Also try with reflected polys on broadcast
            print("\nTesting reflected CRC-32 variants on broadcast frames:")
            unesc0 = bytes([bc_frames[0][0]]) + pseudo_unescape(bc_frames[0][1:-1]) + bytes([bc_frames[0][-1]])
            crc_field = unesc0[-5:-1]
            exp_be = struct.unpack(">I", crc_field)[0]
            exp_le = struct.unpack("<I", crc_field)[0]

            for desc, data in [("[1:-5]", unesc0[1:-5]), ("[2:-5]", unesc0[2:-5])]:
                for poly_name, poly in polys.items():
                    for init in [0x00000000, 0xFFFFFFFF]:
                        for xor in [0x00000000, 0xFFFFFFFF]:
                            crc = crc32_reflected(data, poly, init, xor)
                            if crc == exp_be or crc == exp_le:
                                endian = "BE" if crc == exp_be else "LE"
                                print(f"  ✅ {desc} {poly_name} init=0x{init:08X} xor=0x{xor:08X} → {endian}")
                                # Verify on 2 more frames
                                ok = True
                                for j in range(1, min(3, len(bc_frames))):
                                    u = bytes([bc_frames[j][0]]) + pseudo_unescape(bc_frames[j][1:-1]) + bytes([bc_frames[j][-1]])
                                    cf = u[-5:-1]
                                    e = struct.unpack(f"{'>' if endian=='BE' else '<'}I", cf)[0]
                                    if desc == "[1:-5]":
                                        d = u[1:-5]
                                    else:
                                        d = u[2:-5]
                                    c = crc32_reflected(d, poly, init, xor)
                                    if c != e:
                                        ok = False
                                        break
                                if ok:
                                    print(f"       🎉 VERIFIED on multiple broadcast frames!")

    # Also check: maybe the "CRC" in command frames isn't CRC at all on the payload
    # but on the frame's RAW bytes including surrounding bus context?
    # Or maybe it's computed BEFORE escaping (but our cmd frames have no escapes)

    # Final idea: maybe the last 4 bytes AREN'T all CRC. Maybe last 2 = CRC-16,
    # and bytes 17-18 = some other field?
    print("\n\n" + "=" * 70)
    print("2-BYTE CRC HYPOTHESIS (maybe bytes 19-20 or 17-18 are CRC-16)")
    print("=" * 70)

    for name, frame in COMMANDS:
        data = frame[1:17]
        # Modbus CRC-16
        crc16 = 0xFFFF
        for byte in data:
            crc16 ^= byte
            for _ in range(8):
                if crc16 & 1:
                    crc16 = (crc16 >> 1) ^ 0xA001
                else:
                    crc16 >>= 1
        crc16 &= 0xFFFF

        crc_field = frame[17:21]
        field_16_1 = (crc_field[0] << 8) | crc_field[1]  # bytes 17-18 as BE
        field_16_2 = (crc_field[2] << 8) | crc_field[3]  # bytes 19-20 as BE
        field_16_1_le = crc_field[0] | (crc_field[1] << 8)
        field_16_2_le = crc_field[2] | (crc_field[3] << 8)

        match1 = "✅" if crc16 == field_16_1 or crc16 == field_16_1_le else "  "
        match2 = "✅" if crc16 == field_16_2 or crc16 == field_16_2_le else "  "
        print(f"  {name:12s}: modbus16=0x{crc16:04X} "
              f"field[17:19]=0x{field_16_1:04X}/0x{field_16_1_le:04X} {match1} "
              f"field[19:21]=0x{field_16_2:04X}/0x{field_16_2_le:04X} {match2}")


if __name__ == "__main__":
    main()

