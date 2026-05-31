"""Focused date-write test for Joyonway P25B85.

Investigates whether the spa controller can accept date changes via the 0xA2
DateTime command. Previous tests showed time (H:M:S) updates instantly but the
date (Y:M:D) appears unchanged. This script tries several approaches:

1. Standard 0xA2 command with a different day (baseline)
2. Varying byte 7 (the "0x50 prefix") — maybe it controls what fields apply
3. Varying bytes 14-15 (padding) — maybe they're flags
4. Sending date-only (keep same H:M:S, change only date)

Usage:
    source .venv/bin/activate
    python tools/test_date_write.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from importlib.util import module_from_spec, spec_from_file_location
import types

_comp_dir = Path(__file__).resolve().parent.parent / "custom_components" / "joyonway"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_pkg = types.ModuleType("joyonway")
_pkg.__path__ = [str(_comp_dir)]
sys.modules["joyonway"] = _pkg

_adapters_pkg = types.ModuleType("joyonway.adapters")
_adapters_pkg.__path__ = [str(_comp_dir / "adapters")]
sys.modules["joyonway.adapters"] = _adapters_pkg

_load("joyonway.adapters.base", _comp_dir / "adapters" / "base.py")
_load("joyonway.protocol", _comp_dir / "protocol.py")
_load("joyonway.adapters.p25b85", _comp_dir / "adapters" / "p25b85.py")

from joyonway.adapters.p25b85 import P25B85Adapter
from joyonway.protocol import (
    build_frame,
    find_frames,
    is_broadcast,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
BROADCAST_TIMEOUT = 5.0
POST_COMMAND_DELAY = 2.5

adapter = P25B85Adapter()

# ANSI
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✅{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}❌{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ️  {msg}{RESET}")


async def drain_stale(reader: asyncio.StreamReader) -> None:
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            if not chunk:
                break
        except asyncio.TimeoutError:
            break


async def read_broadcast(reader: asyncio.StreamReader) -> dict | None:
    """Read broadcasts until we get a valid parsed one. Returns parsed dict."""
    buf = bytearray()
    deadline = time.monotonic() + BROADCAST_TIMEOUT
    latest = None

    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(
                reader.read(4096),
                timeout=deadline - time.monotonic(),
            )
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
            buf = buf[last_end + 1:]

        for raw_frame in raw_frames:
            if not validate_frame(raw_frame):
                continue
            if not is_broadcast(raw_frame):
                continue
            try:
                logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
                result = adapter.parse_status(logical)
                if result is not None:
                    latest = result
                    # Also store raw logical for byte inspection
                    latest["_logical_bytes"] = logical
            except Exception:
                continue

        if latest is not None and not buf:
            break

    return latest


async def send_raw_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd: bytes,
    description: str,
) -> dict | None:
    """Send command, wait, read broadcast, return parsed state."""
    await drain_stale(reader)
    info(f"Sending: {description}")
    info(f"  Wire hex: {cmd.hex()}")
    writer.write(cmd)
    await writer.drain()
    await asyncio.sleep(POST_COMMAND_DELAY)
    return await read_broadcast(reader)


def build_datetime_raw(
    prefix: int,
    year_offset: int,
    month: int,
    day: int,
    hour: int,
    minute: int,
    second: int,
    pad14: int = 0x00,
    pad15: int = 0x00,
) -> bytes:
    """Build a DateTime (0xA2) command with configurable prefix and padding."""
    payload = bytearray([
        0x01, 0x20, 0x10, 0x3C, 0xA2, 0x10, 0xA1,
        prefix,
        year_offset,
        month,
        day,
        hour,
        minute,
        second,
        pad14,
        pad15,
    ])
    return build_frame(bytes(payload))


def show_datetime(state: dict | None, label: str) -> None:
    """Print the spa_datetime from a parsed broadcast state."""
    if state is None:
        fail(f"{label}: No broadcast received")
        return
    dt = state.get("spa_datetime")
    if dt is None:
        fail(f"{label}: spa_datetime is None")
        return
    print(f"  {BOLD}{label}:{RESET} {dt.year}-{dt.month:02d}-{dt.day:02d} "
          f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}")
    # Also show raw bytes 53-58 for analysis
    logical = state.get("_logical_bytes")
    if logical and len(logical) > 58:
        dt_raw = logical[53:59]
        print(f"    Raw datetime bytes [53:59]: {dt_raw.hex()} "
              f"= [{', '.join(f'0x{b:02x}' for b in dt_raw)}]")


async def run():
    if not HOST:
        print(f"{RED}ERROR: Set SPA_BRIDGE_HOST in .env{RESET}")
        return

    print(f"\n{'='*60}")
    print(f"  {BOLD}DATE WRITE TEST — Joyonway P25B85{RESET}")
    print(f"{'='*60}")
    print(f"  Bridge: {HOST}:{PORT}")
    print()

    reader, writer = await asyncio.open_connection(HOST, PORT)
    info("Connected to bridge")

    try:
        # Step 1: Read current state
        print(f"\n{'─'*60}")
        print(f"  {BOLD}STEP 1: Read current spa clock{RESET}")
        state = await read_broadcast(reader)
        show_datetime(state, "Current spa clock")

        if state is None or state.get("spa_datetime") is None:
            fail("Cannot proceed without current datetime")
            return

        spa_dt = state["spa_datetime"]
        orig_year = spa_dt.year
        orig_month = spa_dt.month
        orig_day = spa_dt.day
        orig_hour = spa_dt.hour
        orig_min = spa_dt.minute
        orig_sec = spa_dt.second

        # Choose a test date that's clearly different
        # Change day by +1 (or wrap) and set distinctive time
        test_day = (orig_day % 28) + 1  # wrap around safely
        test_hour = 3  # distinctive hour that won't be confused
        test_min = 33
        test_sec = 33

        print(f"\n  Spa currently shows: {orig_year}-{orig_month:02d}-{orig_day:02d} "
              f"{orig_hour:02d}:{orig_min:02d}")
        print(f"  Will try to set:    {orig_year}-{orig_month:02d}-{test_day:02d} "
              f"{test_hour:02d}:{test_min:02d}:{test_sec:02d}")

        input(f"\n  {BOLD}>>> Press ENTER to start date write tests [q to quit]: {RESET}")

        # ─── TEST A: Standard 0xA2 with prefix=0x50 (baseline) ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST A: Standard command (prefix=0x50) — what we always send{RESET}")
        cmd = build_datetime_raw(
            prefix=0x50,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0x50: {orig_year}-{orig_month:02d}-{test_day:02d} "
            f"{test_hour:02d}:{test_min:02d}:{test_sec:02d}")
        show_datetime(state, "After prefix=0x50")

        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            time_ok = (dt.hour == test_hour and dt.minute == test_min)
            date_ok = (dt.day == test_day)
            if time_ok:
                ok("Time changed (confirms command was received)")
            else:
                fail(f"Time did NOT change (H:M = {dt.hour}:{dt.minute})")
            if date_ok:
                ok(f"DATE CHANGED to day {test_day}! Prefix 0x50 DOES set the date!")
            else:
                info(f"Date stayed at day {dt.day} (did not change to {test_day})")

        await asyncio.sleep(1.0)

        # ─── TEST B: Try prefix=0x70 ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST B: prefix=0x70 (maybe bit 5 enables date write){RESET}")
        cmd = build_datetime_raw(
            prefix=0x70,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 1,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0x70")
        show_datetime(state, "After prefix=0x70")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with prefix=0x70!")
            else:
                info(f"Day still {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST C: Try prefix=0xD0 ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST C: prefix=0xD0 (maybe bit 7 enables date write){RESET}")
        cmd = build_datetime_raw(
            prefix=0xD0,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 2,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0xD0")
        show_datetime(state, "After prefix=0xD0")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with prefix=0xD0!")
            else:
                info(f"Day still {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST D: Try prefix=0xF0 ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST D: prefix=0xF0 (all high bits){RESET}")
        cmd = build_datetime_raw(
            prefix=0xF0,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 3,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0xF0")
        show_datetime(state, "After prefix=0xF0")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with prefix=0xF0!")
            else:
                info(f"Day still {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST E: Try pad bytes = 0x01 (maybe flags) ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST E: prefix=0x50, pad14=0x01 (maybe a 'set date' flag){RESET}")
        cmd = build_datetime_raw(
            prefix=0x50,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 4,
            pad14=0x01,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0x50 pad14=0x01")
        show_datetime(state, "After pad14=0x01")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with pad14=0x01!")
            else:
                info(f"Day still {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST F: Try prefix=0x00 ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST F: prefix=0x00 (no prefix flags at all){RESET}")
        cmd = build_datetime_raw(
            prefix=0x00,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 5,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 prefix=0x00")
        show_datetime(state, "After prefix=0x00")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with prefix=0x00!")
            else:
                info(f"Day still {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST G: Try different date format — maybe day is BCD ───
        print(f"\n{'─'*60}")
        test_day_bcd = int(f"{test_day:02d}", 16)  # e.g., day 28 → 0x28
        print(f"  {BOLD}TEST G: Day as BCD (day {test_day} → byte 0x{test_day_bcd:02x}){RESET}")
        cmd = build_datetime_raw(
            prefix=0x50,
            year_offset=orig_year - 2000,
            month=orig_month,
            day=test_day_bcd,  # BCD encoding
            hour=test_hour,
            minute=test_min,
            second=test_sec + 6,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 day as BCD=0x{test_day_bcd:02x}")
        show_datetime(state, "After BCD day")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with BCD encoding!")
            else:
                info(f"Day is {dt.day}")

        await asyncio.sleep(1.0)

        # ─── TEST H: Try with year/month also BCD ───
        print(f"\n{'─'*60}")
        year_bcd = int(f"{orig_year - 2000:02d}", 16)  # 26 → 0x26
        month_bcd = int(f"{orig_month:02d}", 16)  # 5 → 0x05 (same)
        print(f"  {BOLD}TEST H: All date fields as BCD "
              f"(yr=0x{year_bcd:02x} mo=0x{month_bcd:02x} dy=0x{test_day_bcd:02x}){RESET}")
        cmd = build_datetime_raw(
            prefix=0x50,
            year_offset=year_bcd,
            month=month_bcd,
            day=test_day_bcd,
            hour=test_hour,
            minute=test_min,
            second=test_sec + 7,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"0xA2 all BCD: year=0x{year_bcd:02x} month=0x{month_bcd:02x} day=0x{test_day_bcd:02x}")
        show_datetime(state, "After all-BCD")
        if state and state.get("spa_datetime"):
            dt = state["spa_datetime"]
            if dt.day == test_day:
                ok(f"DATE CHANGED with all-BCD encoding!")
            else:
                info(f"Day is {dt.day}")

        # ─── RESTORE ───
        print(f"\n{'─'*60}")
        print(f"  {BOLD}RESTORE: Setting clock back to current time{RESET}")
        now = datetime.now()
        cmd = build_datetime_raw(
            prefix=0x50,
            year_offset=now.year - 2000,
            month=now.month,
            day=now.day,
            hour=now.hour,
            minute=now.minute,
            second=now.second,
        )
        state = await send_raw_command(reader, writer, cmd,
            f"Restore: {now.strftime('%Y-%m-%d %H:%M:%S')}")
        show_datetime(state, "After restore")

        # ─── SUMMARY ───
        print(f"\n{'='*60}")
        print(f"  {BOLD}ANALYSIS{RESET}")
        print(f"{'='*60}")
        print(f"""
  If ALL tests show the date unchanged, the controller truly ignores
  the Y/M/D fields in the 0xA2 command. Possible explanations:
  
  1. The date is maintained by a separate RTC chip that auto-increments
     at midnight. The controller firmware only exposes time-set, not date-set.
  2. There's a completely different command type for setting the date
     (not 0xA2 — maybe undiscovered).
  3. The date needs a specific sequence (like the ozone two-step).
  
  Check the PB554 panel: does IT have a way to set the date? If so,
  we need to capture what it sends when the date is changed manually.
  If the panel cannot set the date either, it's likely hardware-limited.
""")

    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        info("Disconnected")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n  Interrupted.")


