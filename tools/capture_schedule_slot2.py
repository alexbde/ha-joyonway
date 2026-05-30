#!/usr/bin/env python3
"""Capture schedule time changes on DISABLED slot 2 via the PB554 panel.

Purpose: Determine whether the controller accepts time changes for disabled
slot 2 when initiated from the physical panel (PB554). This will confirm
or refute the hypothesis that the controller ignores slot 2 time values
when the slot is disabled in the flags byte.

Procedure:
  1. Connects to EW11, reads baseline (current schedule state)
  2. Prompts you to change heat slot 2 START time on the panel
  3. Captures the command frames sent by the panel
  4. Reads broadcast to check if the value changed
  5. Repeats for heat slot 2 END, filter slot 2 START, filter slot 2 END
  6. Saves all captured frames to a JSONL log

Usage:
    source .venv/bin/activate
    python tools/capture_schedule_slot2.py

Requires .env with SPA_BRIDGE_HOST (and optionally SPA_BRIDGE_PORT).
"""
from __future__ import annotations

import asyncio
import json
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

_comp_dir = Path(__file__).resolve().parent.parent / "custom_components" / "joyonway_p25b85"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Set up package hierarchy so relative imports work
_pkg = types.ModuleType("joyonway_p25b85")
_pkg.__path__ = [str(_comp_dir)]
sys.modules["joyonway_p25b85"] = _pkg

_adapters_pkg = types.ModuleType("joyonway_p25b85.adapters")
_adapters_pkg.__path__ = [str(_comp_dir / "adapters")]
sys.modules["joyonway_p25b85.adapters"] = _adapters_pkg

# Load modules
_load("joyonway_p25b85.adapters.base", _comp_dir / "adapters" / "base.py")
_load("joyonway_p25b85.protocol", _comp_dir / "protocol.py")
_load("joyonway_p25b85.adapters.p25b85", _comp_dir / "adapters" / "p25b85.py")

from joyonway_p25b85.adapters.p25b85 import P25B85Adapter
from joyonway_p25b85.protocol import (
    FRAME_START,
    FRAME_END,
    find_frames,
    is_broadcast,
    pseudo_unescape,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
BROADCAST_TIMEOUT = 5.0

adapter = P25B85Adapter()

# ─── Capture log ─────────────────────────────────────────────────
CAPTURE_DIR = Path(__file__).resolve().parent / "captures_schedule_slot2"
CAPTURE_DIR.mkdir(exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = CAPTURE_DIR / f"capture_slot2_{ts}.jsonl"
_log_file = None

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _log_event(event_type: str, **kwargs) -> None:
    if _log_file is None:
        return
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "t": time.monotonic(),
        "event": event_type,
        **kwargs,
    }
    _log_file.write(json.dumps(record) + "\n")
    _log_file.flush()


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ️  {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✅ {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}❌ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


async def drain_stale(reader: asyncio.StreamReader) -> None:
    """Drain any buffered data from the TCP socket."""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            if not chunk:
                break
        except asyncio.TimeoutError:
            break


async def read_broadcast(reader: asyncio.StreamReader) -> dict | None:
    """Read from TCP stream until we get a valid P25B85 broadcast frame."""
    deadline = time.monotonic() + BROADCAST_TIMEOUT
    buf = bytearray()
    latest_result: dict | None = None

    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(
                reader.read(4096),
                timeout=max(0.1, deadline - time.monotonic()),
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
                    _log_event("broadcast", raw_hex=raw_frame.hex(), logical_hex=logical.hex())
                    latest_result = result
            except Exception:
                continue

        if latest_result is not None and not buf:
            break

    return latest_result


async def capture_commands_until_enter(
    reader: asyncio.StreamReader, description: str
) -> list[bytes]:
    """Capture all non-broadcast frames until the user presses ENTER.

    Returns list of raw command frame bytes captured from the panel.
    """
    print(f"\n  {BOLD}>>> Now change '{description}' on the PB554 panel.{RESET}")
    print(f"  {BOLD}>>> Press ENTER when done (after the panel confirms the change).{RESET}")

    captured_commands: list[bytes] = []
    stop_event = asyncio.Event()

    async def _stdin_waiter():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sys.stdin.readline)
        stop_event.set()

    stdin_task = asyncio.create_task(_stdin_waiter())
    buf = bytearray()

    try:
        while not stop_event.is_set():
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.2)
            except asyncio.TimeoutError:
                continue
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
                if is_broadcast(raw_frame):
                    # Log broadcasts for context but don't add to commands
                    try:
                        logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
                        result = adapter.parse_status(logical)
                        if result is not None:
                            _log_event("broadcast", raw_hex=raw_frame.hex(), logical_hex=logical.hex())
                    except Exception:
                        pass
                else:
                    # Non-broadcast frame = command from panel
                    captured_commands.append(raw_frame)
                    _log_event("command_captured", raw_hex=raw_frame.hex(), description=description)
                    info(f"  Captured command: {raw_frame.hex()}")
    finally:
        if not stdin_task.done():
            stdin_task.cancel()

    return captured_commands


def format_schedule(data: dict, prefix: str) -> str:
    """Format schedule data for display."""
    s1 = data.get(f"{prefix}_slot1_start", (0, 0))
    e1 = data.get(f"{prefix}_slot1_end", (0, 0))
    s2 = data.get(f"{prefix}_slot2_start", (0, 0))
    e2 = data.get(f"{prefix}_slot2_end", (0, 0))
    s1_en = data.get(f"{prefix}_slot1_enabled", False)
    s2_en = data.get(f"{prefix}_slot2_enabled", False)
    return (
        f"slot1={s1[0]:02d}:{s1[1]:02d}-{e1[0]:02d}:{e1[1]:02d} "
        f"({'ON' if s1_en else 'OFF'}), "
        f"slot2={s2[0]:02d}:{s2[1]:02d}-{e2[0]:02d}:{e2[1]:02d} "
        f"({'ON' if s2_en else 'OFF'})"
    )


async def run() -> None:
    global _log_file

    print(f"\n{'='*70}")
    print(f"  {BOLD}Capture: Schedule Slot 2 Time Changes (Panel){RESET}")
    print(f"{'='*70}")
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Log:  {LOG_PATH}")
    print(f"{'='*70}")
    print()
    print(f"  {BOLD}Purpose:{RESET} Capture what command the PB554 panel sends when")
    print(f"  changing times on a DISABLED slot 2. This will show whether")
    print(f"  the panel uses a different flags byte or command structure")
    print(f"  compared to our integration.")
    print()
    print(f"  {BOLD}Prerequisites:{RESET}")
    print(f"  - Both heat slots should be DISABLED before starting")
    print(f"  - Both filter slots should be DISABLED before starting")
    print(f"  - Make sure HA integration is NOT connected (stop it or")
    print(f"    disconnect so only this script has the TCP connection)")
    print()

    if not HOST:
        print(f"{RED}ERROR: SPA_BRIDGE_HOST not set in .env{RESET}")
        return

    input(f"  {BOLD}>>> Press ENTER to connect and start...{RESET}")

    _log_file = open(LOG_PATH, "a")
    _log_event("session_start", host=HOST, port=PORT)

    info("Connecting to EW11 bridge...")
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        fail(f"Cannot connect: {e}")
        return
    ok("Connected")

    try:
        # ─── BASELINE ─────────────────────────────────────────────
        info("Reading baseline broadcast...")
        await drain_stale(reader)
        state = await read_broadcast(reader)
        if state is None:
            fail("No valid broadcast received")
            return

        print()
        info(f"Heat schedule:   {format_schedule(state, 'heat')}")
        info(f"Filter schedule: {format_schedule(state, 'filter')}")
        _log_event("baseline", heat=format_schedule(state, 'heat'),
                   filter=format_schedule(state, 'filter'))

        # Verify slot 2 is disabled
        h_s2_en = state.get("heat_slot2_enabled", True)
        f_s2_en = state.get("filter_slot2_enabled", True)
        if h_s2_en:
            warn("Heat slot 2 is ENABLED! Please disable it on the panel first.")
            input(f"  {BOLD}>>> Disable heat slot 2, then press ENTER...{RESET}")
            await drain_stale(reader)
            state = await read_broadcast(reader)
        if f_s2_en:
            warn("Filter slot 2 is ENABLED! Please disable it on the panel first.")
            input(f"  {BOLD}>>> Disable filter slot 2, then press ENTER...{RESET}")
            await drain_stale(reader)
            state = await read_broadcast(reader)

        # ─── CAPTURE: Heat slot 2 START time change ───────────────
        print(f"\n{'─'*70}")
        print(f"  {BOLD}STEP 1: Change HEAT slot 2 START time on panel{RESET}")
        print(f"  Current heat slot 2 start: {state.get('heat_slot2_start', '?')}")
        print(f"  Change it to any DIFFERENT time (e.g. +1 hour)")

        cmds = await capture_commands_until_enter(reader, "heat slot 2 START time")
        info(f"Captured {len(cmds)} command frame(s)")

        await drain_stale(reader)
        new_state = await read_broadcast(reader)
        if new_state:
            old_val = state.get("heat_slot2_start")
            new_val = new_state.get("heat_slot2_start")
            if new_val != old_val:
                ok(f"Heat slot 2 start CHANGED: {old_val} → {new_val}")
            else:
                warn(f"Heat slot 2 start UNCHANGED: {new_val} (controller may have ignored it)")
            info(f"Full heat schedule: {format_schedule(new_state, 'heat')}")
            state = new_state

        # ─── CAPTURE: Heat slot 2 END time change ────────────────
        print(f"\n{'─'*70}")
        print(f"  {BOLD}STEP 2: Change HEAT slot 2 END time on panel{RESET}")
        print(f"  Current heat slot 2 end: {state.get('heat_slot2_end', '?')}")
        print(f"  Change it to any DIFFERENT time")

        cmds = await capture_commands_until_enter(reader, "heat slot 2 END time")
        info(f"Captured {len(cmds)} command frame(s)")

        await drain_stale(reader)
        new_state = await read_broadcast(reader)
        if new_state:
            old_val = state.get("heat_slot2_end")
            new_val = new_state.get("heat_slot2_end")
            if new_val != old_val:
                ok(f"Heat slot 2 end CHANGED: {old_val} → {new_val}")
            else:
                warn(f"Heat slot 2 end UNCHANGED: {new_val}")
            info(f"Full heat schedule: {format_schedule(new_state, 'heat')}")
            state = new_state

        # ─── CAPTURE: Filter slot 2 START time change ─────────────
        print(f"\n{'─'*70}")
        print(f"  {BOLD}STEP 3: Change FILTER slot 2 START time on panel{RESET}")
        print(f"  Current filter slot 2 start: {state.get('filter_slot2_start', '?')}")
        print(f"  Change it to any DIFFERENT time")

        cmds = await capture_commands_until_enter(reader, "filter slot 2 START time")
        info(f"Captured {len(cmds)} command frame(s)")

        await drain_stale(reader)
        new_state = await read_broadcast(reader)
        if new_state:
            old_val = state.get("filter_slot2_start")
            new_val = new_state.get("filter_slot2_start")
            if new_val != old_val:
                ok(f"Filter slot 2 start CHANGED: {old_val} → {new_val}")
            else:
                warn(f"Filter slot 2 start UNCHANGED: {new_val}")
            info(f"Full filter schedule: {format_schedule(new_state, 'filter')}")
            state = new_state

        # ─── CAPTURE: Filter slot 2 END time change ──────────────
        print(f"\n{'─'*70}")
        print(f"  {BOLD}STEP 4: Change FILTER slot 2 END time on panel{RESET}")
        print(f"  Current filter slot 2 end: {state.get('filter_slot2_end', '?')}")
        print(f"  Change it to any DIFFERENT time")

        cmds = await capture_commands_until_enter(reader, "filter slot 2 END time")
        info(f"Captured {len(cmds)} command frame(s)")

        await drain_stale(reader)
        new_state = await read_broadcast(reader)
        if new_state:
            old_val = state.get("filter_slot2_end")
            new_val = new_state.get("filter_slot2_end")
            if new_val != old_val:
                ok(f"Filter slot 2 end CHANGED: {old_val} → {new_val}")
            else:
                warn(f"Filter slot 2 end UNCHANGED: {new_val}")
            info(f"Full filter schedule: {format_schedule(new_state, 'filter')}")
            state = new_state

        # ─── SUMMARY ─────────────────────────────────────────────
        print(f"\n{'='*70}")
        print(f"  {BOLD}CAPTURE COMPLETE{RESET}")
        print(f"{'='*70}")
        info(f"Final heat schedule:   {format_schedule(state, 'heat')}")
        info(f"Final filter schedule: {format_schedule(state, 'filter')}")
        info(f"Log saved to: {LOG_PATH}")
        print()
        print(f"  {BOLD}Next step:{RESET} Analyze the captured command frames to see")
        print(f"  what flags byte the panel uses when changing disabled slot 2 times.")
        _log_event("session_end")

    except KeyboardInterrupt:
        print(f"\n\n  {YELLOW}⏹️  Aborted by user.{RESET}")
        _log_event("session_abort")
    finally:
        writer.close()
        if _log_file:
            _log_file.close()


if __name__ == "__main__":
    asyncio.run(run())

