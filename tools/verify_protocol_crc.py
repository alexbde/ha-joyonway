#!/usr/bin/env python3
"""Verify protocol.py CRC implementation against all captured frames."""
import json, sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'custom_components', 'joyonway'))
from protocol import compute_crc, build_frame, pseudo_unescape

ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

def unescape(data):
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x1B and i + 1 < len(data) and data[i + 1] in ESCAPE_MAP:
            result.append(ESCAPE_MAP[data[i + 1]])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)

base = os.path.dirname(__file__)
with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
    data = json.load(f)

print("=" * 60)
print("  CRC Implementation Verification")
print("=" * 60)

# Test 1: CRC computation
ok = 0
total = 0
seen = set()
for name, hex_str in data["frames"].items():
    raw = bytes.fromhex(hex_str)
    inner = unescape(raw[1:-1])
    key = inner.hex()
    if key in seen:
        continue
    seen.add(key)
    total += 1
    payload = inner[:16]
    actual_crc = int.from_bytes(inner[16:20], "little")
    computed_crc = compute_crc(payload)
    if computed_crc == actual_crc:
        ok += 1
    else:
        print(f"  FAIL: {name} computed=0x{computed_crc:08X} actual=0x{actual_crc:08X}")

print(f"\n  CRC computation: {ok}/{total} unique frames ✓")

# Test 2: Full frame rebuild
ok2 = 0
total2 = 0
for name, hex_str in data["frames"].items():
    raw = bytes.fromhex(hex_str)
    inner = unescape(raw[1:-1])
    payload = inner[:16]
    rebuilt = build_frame(payload)
    total2 += 1
    if rebuilt == raw:
        ok2 += 1
    else:
        print(f"  Frame mismatch: {name}")
        print(f"    original: {raw.hex()}")
        print(f"    rebuilt:  {rebuilt.hex()}")

print(f"  Frame rebuild:   {ok2}/{total2} frames ✓")
print(f"\n{'=' * 60}")

