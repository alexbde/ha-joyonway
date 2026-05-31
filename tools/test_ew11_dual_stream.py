#!/usr/bin/env python3
"""Test whether multiple EW11 connections receive the same data stream."""

import asyncio
import os
from pathlib import Path

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


async def read_for(reader, seconds):
    buf = b""
    deadline = asyncio.get_event_loop().time() + seconds
    while asyncio.get_event_loop().time() < deadline:
        remaining = deadline - asyncio.get_event_loop().time()
        try:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=min(remaining, 0.5))
            if not chunk:
                break
            buf += chunk
        except asyncio.TimeoutError:
            continue
    return buf


async def main():
    print(f"Connecting twice to {HOST}:{PORT}...")
    r1, w1 = await asyncio.open_connection(HOST, PORT)
    r2, w2 = await asyncio.open_connection(HOST, PORT)
    print("Both connections open. Reading 5 seconds of data in parallel...\n")

    d1, d2 = await asyncio.gather(read_for(r1, 5.0), read_for(r2, 5.0))

    print(f"Connection 1: {len(d1)} bytes")
    print(f"Connection 2: {len(d2)} bytes\n")

    if d1 == d2:
        print("✅ IDENTICAL — both connections received exactly the same bytes.")
    else:
        # Try to find alignment
        shorter, longer = (d1, d2) if len(d1) <= len(d2) else (d2, d1)
        # Check if shorter is contained in longer
        if shorter[:60] in longer:
            idx = longer.index(shorter[:60])
            print(f"✅ Same data stream — connection 2 started {idx} bytes later.")
            print("   Both receive the full RS485 broadcast (just different start timing).")
        else:
            # Show hex comparison
            print("First 100 bytes of each:")
            print(f"  conn1: {d1[:100].hex()}")
            print(f"  conn2: {d2[:100].hex()}")
            # Check byte-level overlap
            matches = sum(1 for a, b in zip(d1[:200], d2[:200]) if a == b)
            print(f"\n  Byte match rate (first 200): {matches}/{min(200, len(d1), len(d2))}")
            if matches > 150:
                print("  ✅ Mostly the same stream with slight timing jitter.")
            else:
                print("  ⚠️  Streams appear different — bridge may multiplex differently.")

    w1.close()
    w2.close()
    await w1.wait_closed()
    await w2.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())

