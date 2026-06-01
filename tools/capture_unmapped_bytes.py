#!/usr/bin/env python3
"""Developer utility to capture, analyze, and track unmapped broadcast frame bytes.

Helps reverse engineer undocumented controller registers (such as firmware versions).
"""

import argparse
import asyncio
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path

# Add custom_components to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "joyonway"))
from protocol import find_frames, unescape_frame, is_broadcast

# Load environment variables from .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT_RAW = os.environ.get("SPA_BRIDGE_PORT", "8899")

# Exactly matches the mapped indexes in p25b85.py
_MAPPED_INDEXES = {
    0, 1, 2, 3, 4, 5, 6, 7, 8,  # signature
    9,                          # water temp
    12, 13, 14, 16, 17, 28,     # pump, ozone, heater, setpoint, light, activity
    19, 20, 21, 22, 23, 24, 25, 26,  # heat schedule
    29, 30, 31, 32, 33, 34, 35, 36,  # filter schedule
    53, 54, 55, 56, 57, 58,     # datetime
}


async def main():
    parser = argparse.ArgumentParser(description="Analyze unmapped broadcast frame bytes.")
    parser.add_argument("--host", default=HOST, help="IP address of the EW11 bridge")
    parser.add_argument("--port", type=int, default=int(PORT_RAW) if PORT_RAW else 8899, help="TCP port")
    parser.add_argument("--count", type=int, default=10, help="Number of frames to capture for analysis")
    args = parser.parse_args()

    if not args.host:
        print("ERROR: IP address of EW11 bridge not specified. Use --host or set SPA_BRIDGE_HOST in .env")
        sys.exit(1)

    print(f"Connecting to EW11 bridge at {args.host}:{args.port}...")
    try:
        reader, writer = await asyncio.open_connection(args.host, args.port)
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    print(f"Capturing {args.count} broadcast frames...")
    captured_frames = []
    buffer = b""

    try:
        while len(captured_frames) < args.count:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=10.0)
            if not chunk:
                break
            buffer += chunk
            frames = find_frames(buffer)
            for raw_frame in frames:
                unescaped = unescape_frame(raw_frame, full=True)
                if not is_broadcast(unescaped) or len(unescaped) < 30:
                    continue

                captured_frames.append(unescaped)
                print(f"  Captured frame {len(captured_frames)}/{args.count} (len={len(unescaped)})")
                if len(captured_frames) >= args.count:
                    break

            if frames:
                last_end = buffer.rfind(b'\x1d') + 1
                buffer = buffer[last_end:]
    except asyncio.TimeoutError:
        print("WARNING: Capture timed out waiting for frames.")
    finally:
        writer.close()
        await writer.wait_closed()

    if not captured_frames:
        print("ERROR: No broadcast frames were captured.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print(" UNMAPPED BYTES ANALYSIS")
    print("=" * 60)

    # Track seen values for each unmapped index
    unmapped_values = defaultdict(set)
    first_frame = captured_frames[0]
    _TRAILER_LEN = 5
    payload_end = max(0, len(first_frame) - _TRAILER_LEN)
    unmapped_indices = [i for i in range(payload_end) if i not in _MAPPED_INDEXES]

    for frame in captured_frames:
        for idx in unmapped_indices:
            if idx < len(frame):
                unmapped_values[idx].add(frame[idx])

    # Print summary table
    print(f"{'Index':<7} | {'State':<10} | {'Seen Hex Values':<30} | {'Semantic Context (Guess)':<30}")
    print("-" * 85)

    for idx in sorted(unmapped_indices):
        values = sorted(list(unmapped_values[idx]))
        hex_vals = ", ".join([f"0x{v:02X}" for v in values])
        state = "STATIC" if len(values) == 1 else f"CHANGED ({len(values)})"

        # Provide logical semantic guesses
        guess = ""
        if idx in (10, 11):
            guess = "Temp/Pump boundary padding?"
        elif idx in (15, 18):
            guess = "Heater/light control boundary?"
        elif idx in range(37, 53):
            guess = "Option flags / system limits?"
        elif idx >= 59:
            guess = "Post-datetime checksum/trailer?"

        print(f"{idx:<7d} | {state:<10} | {hex_vals:<30} | {guess:<30}")

    print("\n" + "=" * 60)
    print(" FRAME HASH FINGERPRINTS")
    print("=" * 60)
    for index, frame in enumerate(captured_frames):
        digest_input = bytearray()
        frame_payload_end = max(0, len(frame) - _TRAILER_LEN)
        for i in range(frame_payload_end):
            if i in _MAPPED_INDEXES:
                continue
            digest_input.extend((i & 0xFF, frame[i]))

        md5_hash = hashlib.md5(bytes(digest_input)).hexdigest()[:8]
        print(f"Frame {index+1:2d} | Length: {len(frame):3d} | Unmapped Hash: {md5_hash}")

    print("\nAnalysis complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nAborted.")
