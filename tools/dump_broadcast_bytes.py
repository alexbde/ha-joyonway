#!/usr/bin/env python3
"""Dump ALL bytes from a broadcast frame to find schedule data positions."""

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "joyonway"))
from protocol import find_frames, unescape_frame, is_broadcast

env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT_RAW = os.environ.get("SPA_BRIDGE_PORT", "8899")
if not HOST:
    raise SystemExit("Missing SPA_BRIDGE_HOST (set it in environment or .env)")
PORT = int(PORT_RAW)


async def main():
    reader, writer = await asyncio.open_connection(HOST, PORT)
    buffer = b""

    try:
        while True:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=5.0)
            if not chunk:
                break
            buffer += chunk
            frames = find_frames(buffer)
            for raw_frame in frames:
                unescaped = unescape_frame(raw_frame, full=True)
                if not is_broadcast(unescaped) or len(unescaped) < 59:
                    continue

                print(f"Frame ({len(unescaped)} bytes):")
                print(f"  Full hex: {unescaped.hex()}")
                print()

                # Annotated dump
                annotations = {
                    0: "START",
                    1: "dest(FF)",
                    8: "model(03)",
                    9: "water_temp_F",
                    12: "pump",
                    14: "heater_state",
                    16: "setpoint_F",
                    17: "light_flags",
                    19: "sched_cfg",
                    28: "activity",
                    29: "filter_cfg",
                    53: "year",
                    54: "month",
                    55: "day",
                    56: "hour",
                    57: "minute",
                    58: "second",
                }

                for i, b in enumerate(unescaped):
                    ann = annotations.get(i, "")
                    # Highlight bytes near schedule area (18-35)
                    marker = " <<<" if 18 <= i <= 35 else ""
                    print(f"  [{i:2d}] 0x{b:02X} ({b:3d})  {ann}{marker}")

                writer.close()
                await writer.wait_closed()
                return

            if frames:
                last_end = buffer.rfind(b'\x1d') + 1
                buffer = buffer[last_end:]

    except asyncio.TimeoutError:
        print("Timeout")
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())

