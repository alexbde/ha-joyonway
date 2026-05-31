#!/usr/bin/env python3
"""
Phase 4 — CRC brute-force on broadcast frames using raw wire bytes.

Tries CRC on the raw (wire) frame bytes, including escape sequences.
Also tries the 'reveng' algebraic method using pairs of broadcast frames
that differ only in the timestamp second byte.
"""
from __future__ import annotations

import struct
import zlib
import os

FRAME_START = 0x1A
FRAME_END = 0x1D


def extract_broadcast_frames(data: bytes) -> list[bytes]:
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
    table = _crc_table(poly)
    crc = init
    for byte in data:
        crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    return (crc ^ xor_out) & 0xFFFFFFFF


def main():
    captures_dir = os.path.join(os.path.dirname(__file__), "..", "captures_phase4")
    baseline_file = os.path.join(captures_dir, "00_cmd_pump_on_baseline.bin")

    with open(baseline_file, "rb") as f:
        raw_data = f.read()

    bc_frames = extract_broadcast_frames(raw_data)
    print(f"Loaded {len(bc_frames)} broadcast frames")
    print()

    # All are 67 bytes raw. CRC is at raw[62:66] (last 4 before 0x1D at pos 66)
    # Data to hash: try raw[1:62] (skip start byte, up to CRC)
    # Also try raw[0:62], raw[2:62], etc.

    frame0 = bc_frames[0]
    print(f"Frame 0: {frame0.hex()}")
    print(f"Length: {len(frame0)} bytes")
    print(f"Last 5 bytes: {frame0[-5:].hex()} (CRC field + end)")
    print()

    # For 67-byte frames: CRC at positions [62:66], frame end at [66]
    crc_offset = len(frame0) - 5  # = 62
    crc_field = frame0[crc_offset:crc_offset+4]
    exp_be = struct.unpack(">I", crc_field)[0]
    exp_le = struct.unpack("<I", crc_field)[0]
    print(f"Expected CRC: BE=0x{exp_be:08X}  LE=0x{exp_le:08X}")
    print()

    # Try zlib CRC-32 on different raw byte ranges
    print("Testing zlib.crc32 on raw byte ranges:")
    ranges = [
        (1, crc_offset),      # skip start
        (2, crc_offset),      # skip start+dest
        (0, crc_offset),      # include start
        (1, crc_offset+1),    # include 1 extra
        (1, crc_offset-1),    # one less
        (3, crc_offset),      # skip start+dest+type?
    ]
    for start, end in ranges:
        data = frame0[start:end]
        crc = zlib.crc32(data) & 0xFFFFFFFF
        match = ""
        if crc == exp_be: match = "✅ BE!"
        elif crc == exp_le: match = "✅ LE!"
        print(f"  raw[{start}:{end}] ({end-start} bytes): 0x{crc:08X} {match}")

    # Try all reflected polys
    print("\nTesting reflected CRC-32 with common polynomials on raw[1:62]:")
    data = frame0[1:crc_offset]
    polys = {
        "0xEDB88320 (CRC-32)": 0xEDB88320,
        "0x82F63B78 (CRC-32C)": 0x82F63B78,
        "0xEB31D82E (CRC-32K)": 0xEB31D82E,
        "0x992C1A4C (CRC-32K2)": 0x992C1A4C,
        "0xC8DF352F (AUTOSAR)": 0xC8DF352F,
        "0xA833982B (CRC-32D)": 0xA833982B,
        "0x814141AB (CRC-32Q)": 0x814141AB,
    }

    for poly_name, poly in polys.items():
        for init in [0x00000000, 0xFFFFFFFF]:
            for xor in [0x00000000, 0xFFFFFFFF]:
                crc = crc32_reflected(data, poly, init, xor)
                if crc == exp_be or crc == exp_le:
                    endian = "BE" if crc == exp_be else "LE"
                    print(f"  ✅ {poly_name} init=0x{init:08X} xor=0x{xor:08X} → {endian}")

    # Maybe the length prefix is subtracted?
    # Or maybe the frame has a "count" byte embedded?
    # Check byte[3] which is 0x3C = 60. Is that a length field?
    print(f"\nFrame byte[3] = 0x{frame0[3]:02X} = {frame0[3]} (possible length field?)")
    print(f"  Frame total = {len(frame0)}, payload after [3] until CRC = {crc_offset - 4} bytes")
    # 67 - 1(start) - 1(end) = 65 inner bytes
    # 60 could be: "60 bytes of payload following this byte" → 3+1+60 = 64... + 4CRC + 1end = 69? No.
    # Actually for command frame: byte[3] = 0x10 = 16. Frame total = 22.
    # 22 - 2(start/end) - 4(CRC) = 16 payload bytes. byte[3] = length of payload!
    print(f"\nCommand frame byte[3] = 0x10 = 16")
    print(f"  Command total = 22, minus 2(delim) minus 4(CRC) = 16 ← matches byte[3]!")
    print(f"\nBroadcast frame byte[3] = 0x3C = 60")
    print(f"  Broadcast total raw = 67, minus 2(delim) minus 4(CRC) = 61 ← off by 1?")
    print(f"  After unescape: 66 total, minus 2(delim) minus 4(CRC) = 60 ← MATCHES!")
    print()
    print("→ byte[3] IS the payload length (of UNESCAPED data between delimiters, excluding CRC)")
    print("→ CRC is likely computed on UNESCAPED payload (without delimiters)")

    # So for broadcast: unescape frame[1:-1], take first 60 bytes, CRC is next 4 bytes
    inner_raw = frame0[1:-1]  # skip start and end
    inner_unesc = pseudo_unescape_local(inner_raw)
    payload_len = inner_unesc[2]  # byte[3] of full frame = byte[2] of inner
    print(f"\nInner unescaped length: {len(inner_unesc)} bytes")
    print(f"Payload length from byte[2]: {payload_len}")
    print(f"Payload: inner_unesc[0:{payload_len+3}] (header + payload)")
    print(f"CRC field: inner_unesc[{payload_len+3}:{payload_len+3+4}]")

    # Extract CRC from unescaped inner
    # Actually: inner = dest + type + len + payload(len bytes) + CRC(4 bytes)
    # So structure: [dest=1][???=1][len=1][payload=len bytes][CRC=4]
    # Wait byte[3] in FULL frame = byte[2] in inner. Let me reconsider.
    #
    # Full frame: [0x1A][dest][byte2][len=byte3][...payload...][CRC4][0x1D]
    # Inner (between delimiters): [dest][byte2][len][...payload...][CRC4]
    # For command frame: [01][20][10][3c a1 10 a1 ...16 bytes payload?][CRC4]
    # Wait byte3=0x10=16, but what are bytes 4-7 in full frame? (3c a1 10 a1)
    # After dest(1) + byte2(1) + len(1) = 3 header bytes in inner:
    # payload = 16 bytes: 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 7d d2 14 → wait that's only 16 bytes total
    # Then CRC = 6b 1d? No, 6b is the last CRC byte and 1d is frame end.

    # Let me recount command frame: 1a 01 20 10 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 7d d2 14 6b 1d
    # inner bytes (skip 1a and 1d): 01 20 10 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 7d d2 14 6b
    # = 20 bytes inner
    # byte[3] in full frame = byte[2] in inner = 0x10 = 16
    # If payload starts at inner byte[3]: 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 = 13 bytes from [3] to [15]
    # Hmm that's only 13. Let me count properly:
    # inner = 01 20 10 | 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 | 7d d2 14 6b
    #          hdr(3)      payload(16 = 0x10)                       CRC(4)
    # Total inner = 3 + 16 + 4 = 23? But inner is 20 bytes...
    # inner = frame[1:-1] = bytes 1..20 of the 22-byte frame = 20 bytes
    # 20 = header? + 16 payload + 4 CRC → header must be 0 bytes?
    # Or: 20 bytes inner, byte[2]=0x10=16, so payload = 16 bytes starting from... ?

    # Let me just count: 01 20 10 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 7d d2 14 6b
    # If len=16: the CRC is the last 4 bytes: 7d d2 14 6b
    # Data before CRC: 01 20 10 3c a1 10 a1 02 02 00 00 00 c0 00 56 00 = 16 bytes
    # But byte[3] says payload is 16... and total-before-CRC is 16... hmm
    # So maybe: CRC covers ALL bytes between delimiters except the CRC itself?

    # For command: CRC over bytes[1:17] = all 16 bytes between start and CRC
    # For broadcast: CRC over bytes[1:62] = all 61 bytes between start and CRC (raw)
    #   or after unescape: CRC over unesc[1:??]

    # Actually for broadcast: unescaped inner is 64 bytes (65 - 1 for the escape)
    # 64 = 60 payload + 4 CRC? So CRC over first 60 bytes of unescaped inner?
    # But byte[3] = 0x3C = 60 and if we say CRC is over bytes[1:-5] of the unescaped frame...

    # Let me just try CRC on the UNESCAPED payload (between delimiters, minus last 4)
    payload_unesc = pseudo_unescape_local(frame0[1:-1])
    data_for_crc = payload_unesc[:-4]
    crc_from_frame = payload_unesc[-4:]

    exp_be2 = struct.unpack(">I", crc_from_frame)[0]
    exp_le2 = struct.unpack("<I", crc_from_frame)[0]

    print(f"\n\nUNESCAPED inner analysis:")
    print(f"  Unescaped inner length: {len(payload_unesc)}")
    print(f"  Data for CRC (all but last 4): {len(data_for_crc)} bytes")
    print(f"  CRC field: {crc_from_frame.hex()} (BE=0x{exp_be2:08X}, LE=0x{exp_le2:08X})")
    print(f"  Data: {data_for_crc.hex()}")

    # Try standard CRC-32 on this
    crc_std = zlib.crc32(data_for_crc) & 0xFFFFFFFF
    print(f"\n  zlib.crc32 = 0x{crc_std:08X} {'✅' if crc_std == exp_be2 or crc_std == exp_le2 else '❌'}")

    for poly_name, poly in polys.items():
        for init in [0x00000000, 0xFFFFFFFF]:
            for xor in [0x00000000, 0xFFFFFFFF]:
                crc = crc32_reflected(data_for_crc, poly, init, xor)
                if crc == exp_be2 or crc == exp_le2:
                    endian = "BE" if crc == exp_be2 else "LE"
                    print(f"  ✅ {poly_name} init=0x{init:08X} xor=0x{xor:08X} → {endian}")

    # Now try on command frames with same approach (bytes between delimiters, minus last 4)
    print("\n\nCommand frame verification (inner bytes minus last 4 = CRC input):")
    COMMANDS = [
        ("PUMP_ON",   bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d")),
        ("PUMP_HIGH", bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d")),
        ("LIGHT",     bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d")),
        ("TEMP_UP",   bytes.fromhex("1a0120103ca110a10000808000c00057005aa3207f1d")),
    ]

    for name, frame in COMMANDS:
        inner = frame[1:-1]  # no escapes in cmd frames
        data_for_crc = inner[:-4]
        crc_field = inner[-4:]
        exp = struct.unpack(">I", crc_field)[0]
        crc_std = zlib.crc32(data_for_crc) & 0xFFFFFFFF
        print(f"  {name:12s}: data={data_for_crc.hex()} CRC={crc_field.hex()} zlib=0x{crc_std:08X} {'✅' if crc_std==exp else '❌'}")

        for poly_name, poly in polys.items():
            for init in [0x00000000, 0xFFFFFFFF]:
                for xor in [0x00000000, 0xFFFFFFFF]:
                    crc = crc32_reflected(data_for_crc, poly, init, xor)
                    if crc == exp or crc == struct.unpack("<I", crc_field)[0]:
                        endian = "BE" if crc == exp else "LE"
                        print(f"    ✅ {poly_name} init=0x{init:08X} xor=0x{xor:08X} → {endian}")

    # Also try: what if the CRC includes the frame start/end bytes?
    # Or: what if byte[2] (0x20) and byte[3] (0x10/0x3C) are part of a different header?
    # Try CRC starting from byte[4]
    print("\nTrying CRC from byte[4] onwards (skip header entirely):")
    for name, frame in COMMANDS:
        data_for_crc = frame[4:17]  # skip 1A, 01, 20, 10
        crc_field = frame[17:21]
        exp = struct.unpack(">I", crc_field)[0]
        for poly_name, poly in polys.items():
            for init in [0x00000000, 0xFFFFFFFF]:
                for xor in [0x00000000, 0xFFFFFFFF]:
                    crc = crc32_reflected(data_for_crc, poly, init, xor)
                    if crc == exp or crc == struct.unpack("<I", crc_field)[0]:
                        endian = "BE" if crc == exp else "LE"
                        print(f"    ✅ {name} {poly_name} init=0x{init:08X} xor=0x{xor:08X} → {endian}")


def pseudo_unescape_local(data: bytes) -> bytes:
    ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}
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


if __name__ == "__main__":
    main()


