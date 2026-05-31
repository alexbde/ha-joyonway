#!/usr/bin/env python3
"""Live test for unresolved schedule case: slot1 ON, slot2 OFF, write slot2 times.

Why this exists:
- The integration currently uses flags=0x62 for (slot1_enabled=True, slot2_enabled=False).
- The slot2 write quirk is fully fixed for both-disabled mode using 0x5A.
- The remaining open question is whether slot2 time writes are accepted with 0x62.

This script validates exactly that scenario for both heat and filter schedules.
It restores original values at the end.

Usage:
    source .venv/bin/activate
    python tools/test_schedule_slot2_s1_on.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path


# Load .env (if present)
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from importlib.util import module_from_spec, spec_from_file_location
import types

_comp_dir = ROOT / "custom_components" / "joyonway_p25b85"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Minimal package setup for relative imports inside integration modules.
_pkg = types.ModuleType("joyonway_p25b85")
_pkg.__path__ = [str(_comp_dir)]
sys.modules["joyonway_p25b85"] = _pkg

_adapters_pkg = types.ModuleType("joyonway_p25b85.adapters")
_adapters_pkg.__path__ = [str(_comp_dir / "adapters")]
sys.modules["joyonway_p25b85.adapters"] = _adapters_pkg

_load("joyonway_p25b85.adapters.base", _comp_dir / "adapters" / "base.py")
_load("joyonway_p25b85.protocol", _comp_dir / "protocol.py")
_load("joyonway_p25b85.adapters.p25b85", _comp_dir / "adapters" / "p25b85.py")

from joyonway_p25b85.adapters.p25b85 import P25B85Adapter
from joyonway_p25b85.protocol import (
    build_frame,
    find_frames,
    is_broadcast,
    pseudo_unescape,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
READ_TIMEOUT = 5.0
AFTER_SEND_DELAY = 2.5
FORCE_FLAGS_S1_ON_S2_OFF = 0x6A

adapter = P25B85Adapter()


def _override_flags(cmd: bytes, flags: int) -> bytes:
    """Rebuild a schedule command frame with a custom flags byte."""
    inner = pseudo_unescape(cmd[1:-1])
    payload = bytearray(inner[:16])
    payload[7] = flags
    return build_frame(bytes(payload))


def _format_slot(state: dict, prefix: str, slot: int) -> str:
    start = state.get(f"{prefix}_slot{slot}_start", (0, 0))
    end = state.get(f"{prefix}_slot{slot}_end", (0, 0))
    enabled = state.get(f"{prefix}_slot{slot}_enabled", False)
    return (
        f"{start[0]:02d}:{start[1]:02d}-{end[0]:02d}:{end[1]:02d}"
        f" ({'ON' if enabled else 'OFF'})"
    )


async def _drain_stale(reader: asyncio.StreamReader) -> None:
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
        except asyncio.TimeoutError:
            return
        if not chunk:
            return


async def _read_broadcast(reader: asyncio.StreamReader) -> dict | None:
    deadline = asyncio.get_event_loop().time() + READ_TIMEOUT
    buf = bytearray()
    latest: dict | None = None

    while asyncio.get_event_loop().time() < deadline:
        timeout = max(0.1, deadline - asyncio.get_event_loop().time())
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=timeout)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break

        buf.extend(chunk)
        raw_frames = find_frames(bytes(buf))
        if not raw_frames:
            continue

        last_end = bytes(buf).rfind(b"\x1d")
        if last_end >= 0:
            buf = buf[last_end + 1 :]

        for raw_frame in raw_frames:
            if not validate_frame(raw_frame) or not is_broadcast(raw_frame):
                continue
            logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
            parsed = adapter.parse_status(logical)
            if parsed is not None:
                latest = parsed

        if latest is not None and not buf:
            break

    return latest


async def _send_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd: bytes,
    description: str,
) -> int:
    await _drain_stale(reader)
    inner = pseudo_unescape(cmd[1:-1])
    flags = inner[7]
    print(f"  TX {description} (flags=0x{flags:02X})")
    writer.write(cmd)
    await writer.drain()
    await asyncio.sleep(AFTER_SEND_DELAY)
    return flags


async def _run_case(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    sched_type: str,
) -> bool:
    print(f"\n--- {sched_type.upper()} ---")
    state = await _read_broadcast(reader)
    if state is None:
        print("  FAIL: no baseline broadcast")
        return False

    s1_start = state[f"{sched_type}_slot1_start"]
    s1_end = state[f"{sched_type}_slot1_end"]
    s2_start = state[f"{sched_type}_slot2_start"]
    s2_end = state[f"{sched_type}_slot2_end"]
    s1_enabled = state[f"{sched_type}_slot1_enabled"]
    s2_enabled = state[f"{sched_type}_slot2_enabled"]

    print(f"  baseline slot1: {_format_slot(state, sched_type, 1)}")
    print(f"  baseline slot2: {_format_slot(state, sched_type, 2)}")

    try:
        # Put schedule in target mode for this test: slot1 ON, slot2 OFF.
        prep = adapter.build_schedule_command(
            sched_type,
            s1_start,
            s1_end,
            s2_start,
            s2_end,
            slot1_enabled=True,
            slot2_enabled=False,
        )
        prep = _override_flags(prep, FORCE_FLAGS_S1_ON_S2_OFF)
        prep_flags = await _send_command(reader, writer, prep, "prepare slot1=ON slot2=OFF")
        prepared = await _read_broadcast(reader)
        if prepared is None:
            print("  FAIL: no broadcast after prepare")
            return False
        if not prepared.get(f"{sched_type}_slot1_enabled") or prepared.get(f"{sched_type}_slot2_enabled"):
            print("  FAIL: could not prepare slot1=ON slot2=OFF")
            print(f"  now slot1: {_format_slot(prepared, sched_type, 1)}")
            print(f"  now slot2: {_format_slot(prepared, sched_type, 2)}")
            return False

        # Change only slot2 time values while keeping slot1 enabled and slot2 disabled.
        test_s2_start = ((s2_start[0] + 2) % 24, 13 if s2_start[1] != 13 else 43)
        test_s2_end = ((s2_end[0] + 2) % 24, 23 if s2_end[1] != 23 else 53)

        cmd = adapter.build_schedule_command(
            sched_type,
            s1_start,
            s1_end,
            test_s2_start,
            test_s2_end,
            slot1_enabled=True,
            slot2_enabled=False,
        )
        cmd = _override_flags(cmd, FORCE_FLAGS_S1_ON_S2_OFF)
        write_flags = await _send_command(reader, writer, cmd, "write slot2 times with slot1=ON slot2=OFF")
        after = await _read_broadcast(reader)
        if after is None:
            print("  FAIL: no broadcast after write")
            return False

        actual_s2_start = after.get(f"{sched_type}_slot2_start")
        actual_s2_end = after.get(f"{sched_type}_slot2_end")
        slot2_applied = (
            (actual_s2_start[0], actual_s2_start[1]) == test_s2_start
            and (actual_s2_end[0], actual_s2_end[1]) == test_s2_end
        )

        print(f"  sent flags: prepare=0x{prep_flags:02X}, write=0x{write_flags:02X}")
        print(f"  expected slot2: {test_s2_start[0]:02d}:{test_s2_start[1]:02d}-{test_s2_end[0]:02d}:{test_s2_end[1]:02d}")
        print(f"  actual   slot2: {actual_s2_start[0]:02d}:{actual_s2_start[1]:02d}-{actual_s2_end[0]:02d}:{actual_s2_end[1]:02d}")
        print(f"  result: {'PASS' if slot2_applied else 'FAIL'}")

        panel = input("  panel confirms slot2 changed? [Y/n]: ").strip().lower()
        panel_ok = panel not in ("n", "no")
        if not panel_ok:
            print("  panel check: FAIL")

        return slot2_applied and panel_ok

    finally:
        # Always restore original schedule state.
        restore = adapter.build_schedule_command(
            sched_type,
            s1_start,
            s1_end,
            s2_start,
            s2_end,
            slot1_enabled=s1_enabled,
            slot2_enabled=s2_enabled,
        )
        await _send_command(reader, writer, restore, "restore original schedule")
        restored = await _read_broadcast(reader)
        if restored is not None:
            print(f"  restored slot1: {_format_slot(restored, sched_type, 1)}")
            print(f"  restored slot2: {_format_slot(restored, sched_type, 2)}")
        else:
            print("  WARN: no broadcast after restore; verify on panel")


async def main() -> None:
    print("\nSchedule slot2 test with slot1 enabled")
    print(f"Target bridge: {HOST}:{PORT}")

    if not HOST:
        print("ERROR: SPA_BRIDGE_HOST is not set (check .env)")
        return

    input("Press ENTER to start (q to abort): ")

    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as exc:
        print(f"ERROR: cannot connect: {exc}")
        return

    heat_ok = False
    filter_ok = False
    try:
        heat_ok = await _run_case(reader, writer, "heat")
        filter_ok = await _run_case(reader, writer, "filter")
    finally:
        writer.close()

    print("\nSummary")
    print(f"  heat:   {'PASS' if heat_ok else 'FAIL'}")
    print(f"  filter: {'PASS' if filter_ok else 'FAIL'}")
    if heat_ok and filter_ok:
        print("  Decision: current 0x62 behavior is acceptable for this scenario.")
    else:
        print("  Decision: collect panel capture for this scenario; 0x68 may be needed.")


if __name__ == "__main__":
    asyncio.run(main())



