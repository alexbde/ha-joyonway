#!/usr/bin/env python3
"""Test schedule slot writes — verify slot 1 vs slot 2 behavior.

Tests the hypothesis that slot 2 time values are ignored by the controller
when slot 2 is disabled (requiring a special force-write flags byte), while
slot 1 times are always accepted regardless of enable state.

Runs systematic write tests for both heat and filter schedules:
  - Write slot 1 times while slot 1 is DISABLED → expect accepted
  - Write slot 2 times while slot 2 is DISABLED (normal flags) → expect ignored
  - Write slot 2 times while slot 2 is DISABLED (force-write) → expect accepted
  - Write slot 2 times while slot 2 is ENABLED → expect accepted

After each test, reads the broadcast to verify what actually changed.
All originals are restored at the end.

All data is captured for later analysis:
  - JSONL log: every event (commands, broadcasts, test results, user input)
  - Raw binary: complete TCP byte stream (all received + sent bytes)

Usage:
    source .venv/bin/activate
    python tools/test_schedule_slots.py

Requires .env with SPA_BRIDGE_HOST (and optionally SPA_BRIDGE_PORT).
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
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
    find_frames,
    is_broadcast,
    pseudo_unescape,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
BROADCAST_TIMEOUT = 5.0
POST_COMMAND_DELAY = 2.5

adapter = P25B85Adapter()

# ─── Capture log + raw binary stream ─────────────────────────────
CAPTURE_DIR = Path(__file__).resolve().parent / "captures_schedule_test"
CAPTURE_DIR.mkdir(exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = CAPTURE_DIR / f"slot_test_{ts}.jsonl"
RAW_BIN_PATH = CAPTURE_DIR / f"slot_test_{ts}_raw.bin"
_log_file = None
_raw_bin_file = None

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


def _record_raw(direction: str, data: bytes) -> None:
    """Append raw bytes to the binary capture file with a framing header.

    Format per record: [direction: 1 byte] [timestamp: 8 bytes double LE]
                       [length: 4 bytes LE] [data: N bytes]
    direction: 0x00 = received from spa, 0x01 = sent to spa
    """
    if _raw_bin_file is None:
        return
    dir_byte = b'\x01' if direction == "tx" else b'\x00'
    ts_bytes = struct.pack('<d', time.time())
    len_bytes = struct.pack('<I', len(data))
    _raw_bin_file.write(dir_byte + ts_bytes + len_bytes + data)
    _raw_bin_file.flush()


def _safe_parsed(parsed: dict) -> dict:
    """Make a parsed broadcast dict JSON-serializable."""
    safe = {}
    for k, v in parsed.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            safe[k] = v
        elif hasattr(v, "isoformat"):
            safe[k] = v.isoformat()
        elif isinstance(v, tuple):
            safe[k] = list(v)
        else:
            safe[k] = str(v)
    return safe


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ️  {msg}{RESET}")


def ok(msg: str) -> None:
    print(f"  {GREEN}✅ {msg}{RESET}")


def fail(msg: str) -> None:
    print(f"  {RED}❌ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


class UserQuit(Exception):
    pass


def wait_enter(prompt: str) -> None:
    resp = input(f"\n  {BOLD}>>> {prompt} [ENTER/q]: {RESET}").strip().lower()
    if resp in ("q", "quit", "exit"):
        raise UserQuit()


def confirm(prompt: str) -> bool:
    resp = input(f"\n  {BOLD}>>> {prompt} [Y/n/q]: {RESET}").strip().lower()
    if resp in ("q", "quit", "exit"):
        raise UserQuit()
    result = resp not in ("n", "no")
    _log_event("user_confirm", prompt=prompt, confirmed=result)
    return result


async def drain_stale(reader: asyncio.StreamReader) -> None:
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            if not chunk:
                break
            _record_raw("rx", chunk)
        except asyncio.TimeoutError:
            break


async def read_broadcast(reader: asyncio.StreamReader) -> dict | None:
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
        _record_raw("rx", chunk)

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
                # Log non-broadcast frames too (controller responses, etc.)
                _log_event("non_broadcast_frame", raw_hex=raw_frame.hex())
                continue
            try:
                logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
                result = adapter.parse_status(logical)
                if result is not None:
                    _log_event("broadcast",
                               raw_hex=raw_frame.hex(),
                               logical_hex=logical.hex(),
                               parsed=_safe_parsed(result))
                    latest_result = result
            except Exception:
                continue

        if latest_result is not None and not buf:
            break

    return latest_result


async def send_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd: bytes,
    description: str,
) -> None:
    await drain_stale(reader)
    info(f"Sending: {description}")
    # Log the command with its unescaped payload for analysis
    inner = cmd[1:-1]  # strip 0x1A / 0x1D delimiters
    unescaped = pseudo_unescape(inner)
    payload_hex = unescaped[:16].hex() if len(unescaped) >= 16 else unescaped.hex()
    flags_byte = unescaped[7] if len(unescaped) > 7 else None
    _log_event("command_sent",
               description=description,
               wire_hex=cmd.hex(),
               payload_hex=payload_hex,
               flags_byte=f"0x{flags_byte:02X}" if flags_byte is not None else None)
    _record_raw("tx", cmd)
    writer.write(cmd)
    await writer.drain()
    await asyncio.sleep(POST_COMMAND_DELAY)


def format_schedule(data: dict, prefix: str) -> str:
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


def check_times(
    state: dict,
    prefix: str,
    slot: int,
    expected_start: tuple[int, int],
    expected_end: tuple[int, int],
) -> tuple[bool, bool]:
    """Check if a slot's start and end match expected values.
    Returns (start_match, end_match).
    """
    actual_start = state.get(f"{prefix}_slot{slot}_start", (-1, -1))
    actual_end = state.get(f"{prefix}_slot{slot}_end", (-1, -1))
    return (
        (actual_start[0], actual_start[1]) == expected_start,
        (actual_end[0], actual_end[1]) == expected_end,
    )


async def run() -> None:
    global _log_file, _raw_bin_file

    print(f"\n{'='*70}")
    print(f"  {BOLD}Schedule Slot Write Test — Joyonway P25B85{RESET}")
    print(f"{'='*70}")
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Log:  {LOG_PATH}")
    print(f"  Raw:  {RAW_BIN_PATH}")
    print(f"{'='*70}")
    print()
    print(f"  {BOLD}What this tests:{RESET}")
    print(f"  1. Write slot 1 times while DISABLED — should be accepted")
    print(f"  2. Write slot 2 times while DISABLED (normal flags) — quirk test")
    print(f"  3. Write slot 2 times while DISABLED (force-write 0x58) — should work")
    print(f"  4. Write both slots while ENABLED — should work")
    print()
    print(f"  Tests both heat (0xA3) and filter (0xA4) schedules.")
    print(f"  All original values are restored at the end.")
    print()
    print(f"  {BOLD}Data capture:{RESET}")
    print(f"  - JSONL log: every command, broadcast (with parsed state),")
    print(f"    test result, and user confirmation")
    print(f"  - Raw binary: complete TCP byte stream (rx + tx with timestamps)")
    print()

    if not HOST:
        print(f"{RED}ERROR: SPA_BRIDGE_HOST not set in .env{RESET}")
        return

    wait_enter("Press ENTER to connect and start...")

    _log_file = open(LOG_PATH, "a")
    _raw_bin_file = open(RAW_BIN_PATH, "ab")
    _log_event("session_start", host=HOST, port=PORT,
               raw_bin_path=str(RAW_BIN_PATH))

    info("Connecting to EW11 bridge...")
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        fail(f"Cannot connect: {e}")
        return
    ok("Connected")

    results: list[tuple[str, bool | None]] = []

    try:
        await _run_tests(reader, writer, results)
    except (UserQuit, KeyboardInterrupt):
        print(f"\n\n  {YELLOW}⏹️  Aborted by user.{RESET}")
    finally:
        # ─── SUMMARY ──────────────────────────────────────────
        print(f"\n{'='*70}")
        print(f"  {BOLD}TEST RESULTS{RESET}")
        print(f"{'='*70}")
        passed = sum(1 for _, r in results if r is True)
        failed = sum(1 for _, r in results if r is False)
        skipped = sum(1 for _, r in results if r is None)
        for name, result in results:
            if result is True:
                print(f"  {GREEN}✅{RESET} {name}")
            elif result is False:
                print(f"  {RED}❌{RESET} {name}")
            else:
                print(f"  {YELLOW}⏭️{RESET}  {name} (skipped)")
        if results:
            print(f"\n  {passed} passed, {failed} failed, {skipped} skipped")
        else:
            print(f"  {YELLOW}No tests completed.{RESET}")
        print(f"{'='*70}")

        if failed > 0:
            print()
            print(f"  {BOLD}Some tests failed!{RESET}")
            print(f"  Run the capture script to record panel behavior:")
            print(f"    python tools/capture_schedule_changes.py")
        print()

        for name, result in results:
            _log_event("test_result", test=name,
                       result="pass" if result is True else "fail" if result is False else "skipped")
        _log_event("session_end")
        if _log_file:
            _log_file.close()
        if _raw_bin_file:
            _raw_bin_file.close()
        writer.close()
        info(f"Log saved:  {LOG_PATH}")
        info(f"Raw saved:  {RAW_BIN_PATH}")


async def _run_tests(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    results: list[tuple[str, bool | None]],
) -> None:
    # ─── Read baseline ─────────────────────────────────────────
    info("Reading baseline broadcast...")
    await drain_stale(reader)
    state = await read_broadcast(reader)
    if state is None:
        fail("No valid broadcast received")
        return
    ok("Broadcast received")
    print()
    info(f"Heat:   {format_schedule(state, 'heat')}")
    info(f"Filter: {format_schedule(state, 'filter')}")

    # Save originals for restore
    orig = {}
    for prefix in ("heat", "filter"):
        for key in ("slot1_start", "slot1_end", "slot2_start", "slot2_end",
                     "slot1_enabled", "slot2_enabled"):
            orig[f"{prefix}_{key}"] = state.get(f"{prefix}_{key}")

    _log_event("baseline", **{k: list(v) if isinstance(v, tuple) else v for k, v in orig.items()})

    # ─── Run tests for both schedule types ─────────────────────
    for sched_type, cmd_label in [("heat", "Heat"), ("filter", "Filter")]:
        s1_start = orig[f"{sched_type}_slot1_start"]
        s1_end = orig[f"{sched_type}_slot1_end"]
        s2_start = orig[f"{sched_type}_slot2_start"]
        s2_end = orig[f"{sched_type}_slot2_end"]
        s1_en = orig[f"{sched_type}_slot1_enabled"]
        s2_en = orig[f"{sched_type}_slot2_enabled"]

        # Compute distinctive test times (shift by +3h, flip minutes)
        test_s1_start = ((s1_start[0] + 3) % 24, 17 if s1_start[1] != 17 else 47)
        test_s1_end = ((s1_end[0] + 3) % 24, 33 if s1_end[1] != 33 else 3)
        test_s2_start = ((s2_start[0] + 3) % 24, 17 if s2_start[1] != 17 else 47)
        test_s2_end = ((s2_end[0] + 3) % 24, 33 if s2_end[1] != 33 else 3)

        # ── TEST A: Write slot 1 times while slot 1 DISABLED ──
        test_name = f"{cmd_label}: slot 1 write while DISABLED"
        print(f"\n{'─'*70}")
        print(f"  {BOLD}TEST: {test_name}{RESET}")
        print(f"  Expecting: slot 1 times accepted (no force flag needed)")
        _log_event("test_start", test=test_name,
                   sent_s1_start=list(test_s1_start), sent_s1_end=list(test_s1_end),
                   sent_s2_start=list(s2_start), sent_s2_end=list(s2_end),
                   s1_enabled=False, s2_enabled=s2_en)

        frame = adapter.build_schedule_command(
            sched_type,
            test_s1_start, test_s1_end,  # new slot 1 times
            s2_start, s2_end,            # keep slot 2 unchanged
            slot1_enabled=False,         # slot 1 DISABLED
            slot2_enabled=s2_en,         # keep slot 2 state
        )
        await send_command(reader, writer, frame, f"{sched_type} schedule: slot1 disabled + new times")
        new_state = await read_broadcast(reader)

        if new_state:
            state = new_state
            s_ok, e_ok = check_times(new_state, sched_type, 1, test_s1_start, test_s1_end)
            actual_s = new_state.get(f"{sched_type}_slot1_start")
            actual_e = new_state.get(f"{sched_type}_slot1_end")
            test_passed = s_ok and e_ok
            if test_passed:
                ok(f"Slot 1 times ACCEPTED while disabled: "
                   f"{actual_s[0]:02d}:{actual_s[1]:02d}-{actual_e[0]:02d}:{actual_e[1]:02d}")
            else:
                fail(f"Slot 1 times NOT applied while disabled!")
                info(f"  Expected: {test_s1_start[0]:02d}:{test_s1_start[1]:02d}-{test_s1_end[0]:02d}:{test_s1_end[1]:02d}")
                info(f"  Got:      {actual_s[0]:02d}:{actual_s[1]:02d}-{actual_e[0]:02d}:{actual_e[1]:02d}")
            _log_event("test_check", test=test_name,
                       expected_start=list(test_s1_start), expected_end=list(test_s1_end),
                       actual_start=list(actual_s), actual_end=list(actual_e),
                       start_match=s_ok, end_match=e_ok, passed=test_passed)
            results.append((test_name, test_passed))
            info(f"Full: {format_schedule(new_state, sched_type)}")
        else:
            fail("No broadcast received")
            _log_event("test_check", test=test_name, error="no_broadcast", passed=False)
            results.append((test_name, False))

        if not confirm(f"Check panel: {sched_type} slot 1 shows new times?"):
            warn("User reports slot 1 times did not change on panel")
            if results and results[-1][0] == test_name:
                results[-1] = (test_name, False)

        # ── TEST B: Write slot 2 times while slot 2 DISABLED (normal flags) ──
        test_name = f"{cmd_label}: slot 2 write while DISABLED (normal flags)"
        print(f"\n{'─'*70}")
        print(f"  {BOLD}TEST: {test_name}{RESET}")
        print(f"  Expecting: slot 2 times IGNORED (this is the quirk)")
        _log_event("test_start", test=test_name,
                   sent_s1_start=list(test_s1_start), sent_s1_end=list(test_s1_end),
                   sent_s2_start=list(test_s2_start), sent_s2_end=list(test_s2_end),
                   s1_enabled=False, s2_enabled=False, force_slot2_write=False)

        frame = adapter.build_schedule_command(
            sched_type,
            test_s1_start, test_s1_end,  # keep slot 1 from test A
            test_s2_start, test_s2_end,  # new slot 2 times
            slot1_enabled=False,         # keep disabled
            slot2_enabled=False,         # slot 2 DISABLED (normal flags → 0x52)
            force_slot2_write=False,     # do NOT use force-write
        )
        await send_command(reader, writer, frame, f"{sched_type} schedule: slot2 disabled, normal flags, new times")
        new_state = await read_broadcast(reader)

        if new_state:
            state = new_state
            s_ok, e_ok = check_times(new_state, sched_type, 2, test_s2_start, test_s2_end)
            actual_s = new_state.get(f"{sched_type}_slot2_start")
            actual_e = new_state.get(f"{sched_type}_slot2_end")
            if not s_ok or not e_ok:
                ok(f"Slot 2 times IGNORED with normal flags (as expected — quirk confirmed)")
                info(f"  Sent:     {test_s2_start[0]:02d}:{test_s2_start[1]:02d}-{test_s2_end[0]:02d}:{test_s2_end[1]:02d}")
                info(f"  Actual:   {actual_s[0]:02d}:{actual_s[1]:02d}-{actual_e[0]:02d}:{actual_e[1]:02d}")
                test_passed = True
            else:
                warn(f"Slot 2 times ACCEPTED with normal flags — quirk NOT confirmed!")
                info(f"  This means slot 2 behaves the same as slot 1.")
                info(f"  The force-write mechanism may not be needed.")
                test_passed = False
            _log_event("test_check", test=test_name,
                       expected_start=list(test_s2_start), expected_end=list(test_s2_end),
                       actual_start=list(actual_s), actual_end=list(actual_e),
                       start_match=s_ok, end_match=e_ok,
                       quirk_expected_ignored=True, was_ignored=not (s_ok and e_ok),
                       passed=test_passed)
            results.append((test_name, test_passed))
            info(f"Full: {format_schedule(new_state, sched_type)}")
        else:
            fail("No broadcast received")
            _log_event("test_check", test=test_name, error="no_broadcast", passed=False)
            results.append((test_name, False))

        # ── TEST C: Write slot 2 times while slot 2 DISABLED (force-write 0x58) ──
        test_name = f"{cmd_label}: slot 2 write while DISABLED (force-write 0x58)"
        print(f"\n{'─'*70}")
        print(f"  {BOLD}TEST: {test_name}{RESET}")
        print(f"  Expecting: slot 2 times ACCEPTED (force-write flags)")
        _log_event("test_start", test=test_name,
                   sent_s1_start=list(test_s1_start), sent_s1_end=list(test_s1_end),
                   sent_s2_start=list(test_s2_start), sent_s2_end=list(test_s2_end),
                   s1_enabled=False, s2_enabled=False, force_slot2_write=True)

        frame = adapter.build_schedule_command(
            sched_type,
            test_s1_start, test_s1_end,  # keep slot 1
            test_s2_start, test_s2_end,  # new slot 2 times
            slot1_enabled=False,         # keep disabled
            slot2_enabled=False,         # slot 2 DISABLED
            force_slot2_write=True,      # USE force-write → flags=0x58
        )
        await send_command(reader, writer, frame, f"{sched_type} schedule: slot2 disabled, FORCE-WRITE, new times")
        new_state = await read_broadcast(reader)

        if new_state:
            state = new_state
            s_ok, e_ok = check_times(new_state, sched_type, 2, test_s2_start, test_s2_end)
            actual_s = new_state.get(f"{sched_type}_slot2_start")
            actual_e = new_state.get(f"{sched_type}_slot2_end")
            test_passed = s_ok and e_ok
            if test_passed:
                ok(f"Slot 2 times ACCEPTED with force-write: "
                   f"{actual_s[0]:02d}:{actual_s[1]:02d}-{actual_e[0]:02d}:{actual_e[1]:02d}")
            else:
                fail(f"Slot 2 times NOT applied even with force-write!")
                info(f"  Expected: {test_s2_start[0]:02d}:{test_s2_start[1]:02d}-{test_s2_end[0]:02d}:{test_s2_end[1]:02d}")
                info(f"  Got:      {actual_s[0]:02d}:{actual_s[1]:02d}-{actual_e[0]:02d}:{actual_e[1]:02d}")
            _log_event("test_check", test=test_name,
                       expected_start=list(test_s2_start), expected_end=list(test_s2_end),
                       actual_start=list(actual_s), actual_end=list(actual_e),
                       start_match=s_ok, end_match=e_ok, passed=test_passed)
            results.append((test_name, test_passed))
            info(f"Full: {format_schedule(new_state, sched_type)}")
        else:
            fail("No broadcast received")
            _log_event("test_check", test=test_name, error="no_broadcast", passed=False)
            results.append((test_name, False))

        if not confirm(f"Check panel: {sched_type} slot 2 shows new times now?"):
            warn("User reports slot 2 times did not change on panel")
            if results and results[-1][0] == test_name:
                results[-1] = (test_name, False)

        # ── TEST D: Write both slots while ENABLED ──
        test_name = f"{cmd_label}: both slots write while ENABLED"
        print(f"\n{'─'*70}")
        print(f"  {BOLD}TEST: {test_name}{RESET}")
        print(f"  Expecting: both slot times accepted")

        # Use slightly different times to distinguish from test C
        test2_s1_start = ((test_s1_start[0] + 1) % 24, test_s1_start[1])
        test2_s1_end = ((test_s1_end[0] + 1) % 24, test_s1_end[1])
        test2_s2_start = ((test_s2_start[0] + 1) % 24, test_s2_start[1])
        test2_s2_end = ((test_s2_end[0] + 1) % 24, test_s2_end[1])

        _log_event("test_start", test=test_name,
                   sent_s1_start=list(test2_s1_start), sent_s1_end=list(test2_s1_end),
                   sent_s2_start=list(test2_s2_start), sent_s2_end=list(test2_s2_end),
                   s1_enabled=True, s2_enabled=True)

        frame = adapter.build_schedule_command(
            sched_type,
            test2_s1_start, test2_s1_end,
            test2_s2_start, test2_s2_end,
            slot1_enabled=True,
            slot2_enabled=True,
        )
        await send_command(reader, writer, frame, f"{sched_type} schedule: both enabled, new times")
        new_state = await read_broadcast(reader)

        if new_state:
            state = new_state
            s1_s_ok, s1_e_ok = check_times(new_state, sched_type, 1, test2_s1_start, test2_s1_end)
            s2_s_ok, s2_e_ok = check_times(new_state, sched_type, 2, test2_s2_start, test2_s2_end)
            all_ok = s1_s_ok and s1_e_ok and s2_s_ok and s2_e_ok
            if all_ok:
                ok(f"Both slot times accepted when enabled")
            else:
                if not s1_s_ok or not s1_e_ok:
                    fail(f"Slot 1 times mismatch")
                if not s2_s_ok or not s2_e_ok:
                    fail(f"Slot 2 times mismatch")
            actual_s1s = new_state.get(f"{sched_type}_slot1_start")
            actual_s1e = new_state.get(f"{sched_type}_slot1_end")
            actual_s2s = new_state.get(f"{sched_type}_slot2_start")
            actual_s2e = new_state.get(f"{sched_type}_slot2_end")
            _log_event("test_check", test=test_name,
                       expected_s1_start=list(test2_s1_start), expected_s1_end=list(test2_s1_end),
                       expected_s2_start=list(test2_s2_start), expected_s2_end=list(test2_s2_end),
                       actual_s1_start=list(actual_s1s), actual_s1_end=list(actual_s1e),
                       actual_s2_start=list(actual_s2s), actual_s2_end=list(actual_s2e),
                       s1_start_match=s1_s_ok, s1_end_match=s1_e_ok,
                       s2_start_match=s2_s_ok, s2_end_match=s2_e_ok,
                       passed=all_ok)
            results.append((test_name, all_ok))
            info(f"Full: {format_schedule(new_state, sched_type)}")
        else:
            fail("No broadcast received")
            _log_event("test_check", test=test_name, error="no_broadcast", passed=False)
            results.append((test_name, False))

        if not confirm(f"Check panel: {sched_type} both slots show new times, both enabled?"):
            warn("User reports schedule did not update correctly on panel")
            if results and results[-1][0] == test_name:
                results[-1] = (test_name, False)

        # ── RESTORE original schedule ──
        print(f"\n  {BOLD}Restoring original {sched_type} schedule...{RESET}")
        _log_event("restore_start", schedule_type=sched_type,
                   s1_start=list(s1_start), s1_end=list(s1_end),
                   s2_start=list(s2_start), s2_end=list(s2_end),
                   s1_enabled=s1_en, s2_enabled=s2_en,
                   force_slot2_write=not s2_en)

        frame = adapter.build_schedule_command(
            sched_type,
            s1_start, s1_end,
            s2_start, s2_end,
            slot1_enabled=s1_en,
            slot2_enabled=s2_en,
            # If slot 2 was disabled, we need force-write to restore times
            force_slot2_write=not s2_en,
        )
        await send_command(reader, writer, frame, f"{sched_type} schedule: RESTORE original")
        new_state = await read_broadcast(reader)
        if new_state:
            state = new_state
            info(f"Restored: {format_schedule(new_state, sched_type)}")
            s1_ok_s, s1_ok_e = check_times(new_state, sched_type, 1, s1_start, s1_end)
            s2_ok_s, s2_ok_e = check_times(new_state, sched_type, 2, s2_start, s2_end)
            _log_event("restore_check", schedule_type=sched_type,
                       s1_start_match=s1_ok_s, s1_end_match=s1_ok_e,
                       s2_start_match=s2_ok_s, s2_end_match=s2_ok_e)
            if s1_ok_s and s1_ok_e:
                ok(f"Slot 1 restored")
            else:
                warn(f"Slot 1 restore may have failed")
            if s2_ok_s and s2_ok_e:
                ok(f"Slot 2 restored")
            else:
                warn(f"Slot 2 restore may have failed — check panel")
        else:
            warn("No broadcast after restore — check panel manually")
            _log_event("restore_check", schedule_type=sched_type, error="no_broadcast")

        if not confirm(f"Confirm {sched_type} schedule is back to original on panel?"):
            warn(f"User reports {sched_type} schedule restore failed — fix manually on panel")


if __name__ == "__main__":
    asyncio.run(run())

