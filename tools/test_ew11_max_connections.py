#!/usr/bin/env python3
"""Test how many simultaneous TCP connections the Elfin EW11 accepts.

Usage:
    python tools/test_ew11_max_connections.py

Reads SPA_BRIDGE_HOST / SPA_BRIDGE_PORT from .env or uses defaults.
Opens connections one by one, reads a bit of data from each to confirm
it's alive, and reports how many succeed before the bridge refuses/drops.
"""

import asyncio
import os
from pathlib import Path

# Load .env
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
MAX_ATTEMPTS = 5
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 3.0


async def try_connect(index: int) -> tuple[int, bool, str, asyncio.StreamReader | None, asyncio.StreamWriter | None]:
    """Try to open a TCP connection and read some data."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(HOST, PORT), timeout=CONNECT_TIMEOUT
        )
    except (asyncio.TimeoutError, OSError) as e:
        return index, False, f"connect failed: {e}", None, None

    # Try to read some data (the EW11 should be forwarding RS485 broadcast)
    try:
        data = await asyncio.wait_for(reader.read(64), timeout=READ_TIMEOUT)
        if data:
            return index, True, f"connected, received {len(data)} bytes", reader, writer
        else:
            return index, False, "connected but got EOF (server closed)", reader, writer
    except asyncio.TimeoutError:
        # Connected but no data within timeout — still counts as connected
        return index, True, "connected (no data yet, but socket alive)", reader, writer


async def main():
    print(f"Testing max TCP connections to EW11 at {HOST}:{PORT}")
    print(f"Will attempt up to {MAX_ATTEMPTS} simultaneous connections.\n")

    connections: list[tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]] = []
    alive_count = 0

    for i in range(1, MAX_ATTEMPTS + 1):
        idx, ok, msg, reader, writer = await try_connect(i)
        print(f"  Connection #{idx}: {msg}")

        if ok:
            alive_count += 1
            connections.append((reader, writer))
        else:
            connections.append((reader, writer))
            # If connect itself failed, stop trying more
            if "connect failed" in msg:
                break

        # Small delay between attempts
        await asyncio.sleep(0.5)

    # Now verify earlier connections are still alive
    print(f"\nVerifying earlier connections still receive data...")
    still_alive = 0
    for i, (reader, writer) in enumerate(connections, 1):
        if reader is None or writer is None:
            continue
        try:
            data = await asyncio.wait_for(reader.read(64), timeout=READ_TIMEOUT)
            if data:
                print(f"  Connection #{i}: still alive ({len(data)} bytes)")
                still_alive += 1
            else:
                print(f"  Connection #{i}: EOF (dropped by bridge)")
        except asyncio.TimeoutError:
            print(f"  Connection #{i}: no data (may still be alive)")
            still_alive += 1
        except Exception as e:
            print(f"  Connection #{i}: error — {e}")

    # Cleanup
    for reader, writer in connections:
        if writer:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    print(f"\n{'='*50}")
    print(f"Result: {alive_count} connection(s) opened successfully")
    print(f"        {still_alive} connection(s) still alive after all opened")
    if alive_count <= 1:
        print("\n⚠️  The EW11 appears to support only 1 TCP connection.")
        print("   Home Assistant will monopolize the link when running.")
        print("   Stop HA or the integration before using capture tools.")
    else:
        print(f"\n✅ The EW11 accepted {alive_count} simultaneous connections.")


if __name__ == "__main__":
    # Make sure HA isn't holding the connection if you want a clean test
    print("NOTE: If Home Assistant is connected, that counts as 1 connection already.\n")
    asyncio.run(main())

