#!/usr/bin/env python3
"""
Test the 1-byte checksum hypothesis from the P23B32 hint against P25B85 frames.

The hint says:
- Checksum = 1 byte, positioned at [-2] (just before 0x1D end byte)
- Calculated as modulo-256 sum OR XOR over payload bytes[1:-2]

Test this against:
1. Our captured command frames (22 bytes)
2. Non-broadcast poll/response frames from bus captures
3. Broadcast frames (67 bytes)
"""
from __future__ import annotations

import os
import struct

# Our captured command frames
COMMANDS = {
    "PUMP_ON":    bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d"),
    "PUMP_HIGH":  bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d"),
    "PUMP_OFF":   bytes.fromhex("1a0120103ca110a10400000000c0005600735738e91d"),
    "LIGHT":      bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d"),
    "TEMP_UP":    bytes.fromhex("1a0120103ca110a10000808000c00057005aa3207f1d"),
    "TEMP_DOWN":  bytes.fromhex("1a0120103ca110a10000808000c0005600dd0ff87e1d"),
}


def calc_mod256(packet: bytes) -> int:
    """Modulo-256 sum of bytes[1:-2]."""
    return sum(packet[1:-2]) % 256


def calc_xor(packet: bytes) -> int:
    """XOR of bytes[1:-2]."""
    result = 0
    for b in packet[1:-2]:
        result ^= b
    return result


def test_checksum_variants(packet: bytes, label: str):
    """Test various 1-byte checksum positions and payload ranges."""
    expected_last = packet[-2]  # byte just before 0x1D

    # Standard approach from hint: payload = bytes[1:-2], checksum = byte[-2]
    mod256 = calc_mod256(packet)
    xor = calc_xor(packet)

    mod256_match = mod256 == expected_last
    xor_match = xor == expected_last

    # Also try: checksum over different ranges
    # Maybe only the "data" after the header (bytes[4:-2] since byte[3]=length)
    mod256_data = sum(packet[4:-2]) % 256
    xor_data = 0
    for b in packet[4:-2]:
        xor_data ^= b

    # Try: two's complement / negation
    neg_mod256 = (256 - (sum(packet[1:-2]) % 256)) % 256
    neg_mod256_data = (256 - (sum(packet[4:-2]) % 256)) % 256

    # Try including start byte
    mod256_full = sum(packet[:-2]) % 256
    xor_full = 0
    for b in packet[:-2]:
        xor_full ^= b

    results = {
        "mod256[1:-2]": mod256,
        "xor[1:-2]": xor,
        "mod256[4:-2]": mod256_data,
        "xor[4:-2]": xor_data,
        "neg_mod256[1:-2]": neg_mod256,
        "neg_mod256[4:-2]": neg_mod256_data,
        "mod256[0:-2]": mod256_full,
        "xor[0:-2]": xor_full,
    }

    matches = [name for name, val in results.items() if val == expected_last]

    return expected_last, results, matches


def extract_frames(data: bytes) -> list[bytes]:
    """Extract all frames from raw data."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == 0x1A:
            j = i + 1
            while j < len(data):
                if data[j] == 0x1D:
                    frames.append(data[i:j+1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def main():
    print("=" * 70)
    print("Testing 1-byte checksum hypothesis against P25B85 frames")
    print("=" * 70)

    # Test against command frames
    print("\n\n1. COMMAND FRAMES (22 bytes)")
    print("-" * 70)
    for name, frame in COMMANDS.items():
        expected, results, matches = test_checksum_variants(frame, name)
        status = f"✅ {matches}" if matches else "❌ no match"
        print(f"\n  {name} (checksum byte[-2] = 0x{expected:02X}):")
        for rname, val in results.items():
            mark = "✅" if val == expected else "  "
            print(f"    {mark} {rname:20s} = 0x{val:02X} ({val:3d})")

    # Test against bus frames from captures
    print("\n\n2. BUS FRAMES FROM CAPTURES")
    print("-" * 70)
    captures_dir = os.path.join(os.path.dirname(__file__), "..", "captures_phase4")
    baseline_file = os.path.join(captures_dir, "00_cmd_pump_on_baseline.bin")

    if os.path.exists(baseline_file):
        with open(baseline_file, "rb") as f:
            raw = f.read()
        all_frames = extract_frames(raw)
        print(f"  Total frames: {len(all_frames)}")

        # Group by length
        by_length: dict[int, list[bytes]] = {}
        for f in all_frames:
            by_length.setdefault(len(f), []).append(f)

        print(f"  Frame lengths: {sorted(by_length.keys())}")

        # Test each length group
        for length in sorted(by_length.keys()):
            frames = by_length[length]
            print(f"\n  --- Length {length} bytes ({len(frames)} frames) ---")

            # Test first few frames of this length
            mod256_hits = 0
            xor_hits = 0
            neg_mod256_hits = 0
            total = min(len(frames), 50)

            for frame in frames[:total]:
                expected = frame[-2]
                mod256 = sum(frame[1:-2]) % 256
                xor = 0
                for b in frame[1:-2]:
                    xor ^= b
                neg_mod = (256 - sum(frame[1:-2]) % 256) % 256

                if mod256 == expected:
                    mod256_hits += 1
                if xor == expected:
                    xor_hits += 1
                if neg_mod == expected:
                    neg_mod256_hits += 1

            print(f"    mod256[1:-2]: {mod256_hits}/{total} match")
            print(f"    xor[1:-2]:    {xor_hits}/{total} match")
            print(f"    neg_mod[1:-2]: {neg_mod256_hits}/{total} match")

            # Also try bytes[4:-2] (after length byte)
            mod256_data_hits = 0
            xor_data_hits = 0
            for frame in frames[:total]:
                expected = frame[-2]
                if len(frame) >= 5:
                    mod256d = sum(frame[4:-2]) % 256
                    xord = 0
                    for b in frame[4:-2]:
                        xord ^= b
                    if mod256d == expected:
                        mod256_data_hits += 1
                    if xord == expected:
                        xor_data_hits += 1

            print(f"    mod256[4:-2]: {mod256_data_hits}/{total} match")
            print(f"    xor[4:-2]:    {xor_data_hits}/{total} match")

            # Show a sample frame
            sample = frames[0]
            print(f"    Sample: {sample.hex()}")
            print(f"    byte[-2] = 0x{sample[-2]:02X}, mod256[1:-2]=0x{sum(sample[1:-2])%256:02X}, "
                  f"xor[1:-2]=0x{calc_xor(sample):02X}")

    # Also try: what if byte[-2] is NOT the checksum, and instead the last 4 bytes
    # contain a checksum differently? E.g., sum of all 4 "CRC" bytes % 256?
    print("\n\n3. ALTERNATIVE: Maybe checksum ISN'T at [-2]")
    print("-" * 70)
    print("  Testing if any single byte in the 'CRC zone' matches a simple checksum:")
    for name, frame in COMMANDS.items():
        print(f"\n  {name}:")
        # What if the checksum is the FIRST of the 4 "CRC" bytes?
        # i.e., it covers bytes[1:17] and the check is at byte[17]
        mod_17 = sum(frame[1:17]) % 256
        xor_17 = 0
        for b in frame[1:17]:
            xor_17 ^= b
        print(f"    byte[17]=0x{frame[17]:02X}  mod256[1:17]=0x{mod_17:02X} {'✅' if mod_17==frame[17] else '  '}  "
              f"xor[1:17]=0x{xor_17:02X} {'✅' if xor_17==frame[17] else '  '}")

        # Checksum at byte[18]?
        mod_18 = sum(frame[1:18]) % 256
        xor_18 = 0
        for b in frame[1:18]:
            xor_18 ^= b
        print(f"    byte[18]=0x{frame[18]:02X}  mod256[1:18]=0x{mod_18:02X} {'✅' if mod_18==frame[18] else '  '}  "
              f"xor[1:18]=0x{xor_18:02X} {'✅' if xor_18==frame[18] else '  '}")

        # What about mod256 of bytes[4:17] at byte[17]?
        mod_4_17 = sum(frame[4:17]) % 256
        xor_4_17 = 0
        for b in frame[4:17]:
            xor_4_17 ^= b
        print(f"    byte[17]=0x{frame[17]:02X}  mod256[4:17]=0x{mod_4_17:02X} {'✅' if mod_4_17==frame[17] else '  '}  "
              f"xor[4:17]=0x{xor_4_17:02X} {'✅' if xor_4_17==frame[17] else '  '}")


if __name__ == "__main__":
    main()

