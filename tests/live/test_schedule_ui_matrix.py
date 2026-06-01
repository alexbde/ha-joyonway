#!/usr/bin/env python3
"""Reliable live test for schedule behavior (HA UI realistic).

This runner validates only scenarios that are actually produced by HA UI:

1) State toggles (slot enable switches):
   - write_mode="state"
   - verifies all 4 enable combinations for heat + filter

2) Time edits (time entities):
   - write_mode="time"
   - keeps current enable combo and changes one field at a time
   - tests all 4 fields (slot1_start, slot1_end, slot2_start, slot2_end)
     for each of the 4 enable combinations, for heat + filter

Reliability improvements:
- per-command retries
- wait-until-broadcast-converges checks
- serialized execution with pacing
- robust restore of original times and enable flags

No manual panel confirmation is requested.

Usage:
    source .venv/bin/activate
    python tests/live/livetest_schedule_ui_matrix.py
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live

import asyncio
from collections.abc import Callable
import json
import os
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Repository root (tests/live -> repo is parents[2])
ROOT = Path(__file__).resolve().parents[2]

# Load .env
env_path = ROOT / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(ROOT))

from importlib.util import module_from_spec, spec_from_file_location
import types

_comp_dir = ROOT / "custom_components" / "joyonway"


def _load(name: str, path: Path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Set up package hierarchy so relative imports work
_pkg = types.ModuleType("joyonway")
_pkg.__path__ = [str(_comp_dir)]
sys.modules["joyonway"] = _pkg

_adapters_pkg = types.ModuleType("joyonway.adapters")
_adapters_pkg.__path__ = [str(_comp_dir / "adapters")]
sys.modules["joyonway.adapters"] = _adapters_pkg

# Load modules
_load("joyonway.adapters.base", _comp_dir / "adapters" / "base.py")
_load("joyonway.protocol", _comp_dir / "protocol.py")
_load("joyonway.adapters.p25b85", _comp_dir / "adapters" / "p25b85.py")

from joyonway.adapters.p25b85 import P25B85Adapter
from joyonway.protocol import (
    find_frames,
    is_broadcast,
    pseudo_unescape,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))

READ_BROADCAST_TIMEOUT = 4.0
WAIT_CONVERGENCE_TIMEOUT = 12.0
POST_COMMAND_DELAY = 2.5
RETRY_DELAY = 1.5
MAX_ATTEMPTS = 3

adapter = P25B85Adapter()

EXPECTED_STATE_FLAGS: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x62,
    (False, True): 0x9A,
    (False, False): 0x52,
}
EXPECTED_TIME_FLAGS: dict[tuple[bool, bool], int] = {
    (True, True): 0xAA,
    (True, False): 0x6A,
    (False, True): 0x9A,
    (False, False): 0x5A,
}

COMBOS: list[tuple[bool, bool]] = [
    (True, True),
    (True, False),
    (False, True),
    (False, False),
]
TIME_FIELDS = ["slot1_start", "slot1_end", "slot2_start", "slot2_end"]

# Output
CAPTURE_DIR = Path(__file__).resolve().parent / "artifacts_schedule_matrix"
CAPTURE_DIR.mkdir(exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = CAPTURE_DIR / f"slot_test_{ts}.jsonl"
RAW_BIN_PATH = CAPTURE_DIR / f"slot_test_{ts}_raw.bin"
_log_file = None
_raw_bin_file = None


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
    if _raw_bin_file is None:
        return
    dir_byte = b"\x01" if direction == "tx" else b"\x00"
    ts_bytes = struct.pack("<d", time.time())
    len_bytes = struct.pack("<I", len(data))
    _raw_bin_file.write(dir_byte + ts_bytes + len_bytes + data)
    _raw_bin_file.flush()


def _safe_parsed(parsed: dict) -> dict:
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


def _fmt_combo(s1: bool, s2: bool) -> str:
    return f"({'ON' if s1 else 'OFF'},{'ON' if s2 else 'OFF'})"


def _times_equal(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return (a[0], a[1]) == (b[0], b[1])


def _next_time_pair(base: tuple[int, int], hour_shift: int, minute_pref: int) -> tuple[int, int]:
    h = (base[0] + hour_shift) % 24
    m = minute_pref if base[1] != minute_pref else (minute_pref + 20) % 60
    return h, m


def _snapshot_schedule(data: dict, prefix: str) -> dict:
    return {
        "slot1_start": data[f"{prefix}_slot1_start"],
        "slot1_end": data[f"{prefix}_slot1_end"],
        "slot2_start": data[f"{prefix}_slot2_start"],
        "slot2_end": data[f"{prefix}_slot2_end"],
        "slot1_enabled": data[f"{prefix}_slot1_enabled"],
        "slot2_enabled": data[f"{prefix}_slot2_enabled"],
    }


def _format_schedule(data: dict, prefix: str) -> str:
    s1 = data.get(f"{prefix}_slot1_start", (0, 0))
    e1 = data.get(f"{prefix}_slot1_end", (0, 0))
    s2 = data.get(f"{prefix}_slot2_start", (0, 0))
    e2 = data.get(f"{prefix}_slot2_end", (0, 0))
    s1_en = data.get(f"{prefix}_slot1_enabled", False)
    s2_en = data.get(f"{prefix}_slot2_enabled", False)
    return (
        f"slot1={s1[0]:02d}:{s1[1]:02d}-{e1[0]:02d}:{e1[1]:02d} ({'ON' if s1_en else 'OFF'}), "
        f"slot2={s2[0]:02d}:{s2[1]:02d}-{e2[0]:02d}:{e2[1]:02d} ({'ON' if s2_en else 'OFF'})"
    )


async def drain_stale(reader: asyncio.StreamReader) -> None:
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            if not chunk:
                break
            _record_raw("rx", chunk)
        except asyncio.TimeoutError:
            break


async def read_broadcast(reader: asyncio.StreamReader, timeout: float = READ_BROADCAST_TIMEOUT) -> dict | None:
    deadline = time.monotonic() + timeout
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
            buf = buf[last_end + 1 :]

        for raw_frame in raw_frames:
            if not validate_frame(raw_frame):
                continue
            if not is_broadcast(raw_frame):
                _log_event("non_broadcast_frame", raw_hex=raw_frame.hex())
                continue
            try:
                logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
                result = adapter.parse_status(logical)
                if result is not None:
                    latest_result = result
                    _log_event(
                        "broadcast",
                        raw_hex=raw_frame.hex(),
                        logical_hex=logical.hex(),
                        parsed=_safe_parsed(result),
                    )
            except Exception:
                continue

        if latest_result is not None and not buf:
            break

    return latest_result


async def wait_for_expected_state(
    reader: asyncio.StreamReader,
    check: Callable[[dict], bool],
    timeout_s: float = WAIT_CONVERGENCE_TIMEOUT,
) -> tuple[bool, dict | None]:
    deadline = time.monotonic() + timeout_s
    last: dict | None = None
    while time.monotonic() < deadline:
        state = await read_broadcast(reader)
        if state is None:
            continue
        last = state
        if check(state):
            return True, state
    return False, last


async def send_schedule_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    schedule_type: str,
    slot1_start: tuple[int, int],
    slot1_end: tuple[int, int],
    slot2_start: tuple[int, int],
    slot2_end: tuple[int, int],
    slot1_enabled: bool,
    slot2_enabled: bool,
    write_mode: str,
    description: str,
) -> int:
    await drain_stale(reader)
    cmd = adapter.build_schedule_command(
        schedule_type,
        slot1_start,
        slot1_end,
        slot2_start,
        slot2_end,
        slot1_enabled=slot1_enabled,
        slot2_enabled=slot2_enabled,
        write_mode=write_mode,
    )
    unesc = pseudo_unescape(cmd[1:-1])
    flags = unesc[7] if len(unesc) > 7 else -1
    _log_event(
        "command_sent",
        description=description,
        schedule_type=schedule_type,
        write_mode=write_mode,
        flags=f"0x{flags:02X}",
        payload_hex=unesc[:16].hex(),
        wire_hex=cmd.hex(),
    )
    _record_raw("tx", cmd)
    writer.write(cmd)
    await writer.drain()
    await asyncio.sleep(POST_COMMAND_DELAY)
    return flags


async def attempt_with_retries(
    action: Callable[[int], asyncio.Future],
    label: str,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[bool, dict | None]:
    last_state: dict | None = None
    for attempt in range(1, max_attempts + 1):
        ok, state = await action(attempt)
        if state is not None:
            last_state = state
        if ok:
            return True, last_state
        _log_event("retry", test=label, attempt=attempt, max_attempts=max_attempts)
        if attempt < max_attempts:
            await asyncio.sleep(RETRY_DELAY)
    return False, last_state


async def ensure_enable_combo(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    schedule: str,
    state: dict,
    target_s1: bool,
    target_s2: bool,
) -> tuple[bool, dict | None]:
    p = schedule
    base = _snapshot_schedule(state, p)
    expected_flags = EXPECTED_STATE_FLAGS[(target_s1, target_s2)]
    label = f"{schedule} state {_fmt_combo(target_s1, target_s2)}"

    async def _action(_attempt: int) -> tuple[bool, dict | None]:
        sent_flags = await send_schedule_command(
            reader,
            writer,
            schedule_type=schedule,
            slot1_start=base["slot1_start"],
            slot1_end=base["slot1_end"],
            slot2_start=base["slot2_start"],
            slot2_end=base["slot2_end"],
            slot1_enabled=target_s1,
            slot2_enabled=target_s2,
            write_mode="state",
            description=label,
        )

        def _ok(st: dict) -> bool:
            return (
                st.get(f"{p}_slot1_enabled") is target_s1
                and st.get(f"{p}_slot2_enabled") is target_s2
                and _times_equal(st.get(f"{p}_slot1_start", (-1, -1)), base["slot1_start"])
                and _times_equal(st.get(f"{p}_slot1_end", (-1, -1)), base["slot1_end"])
                and _times_equal(st.get(f"{p}_slot2_start", (-1, -1)), base["slot2_start"])
                and _times_equal(st.get(f"{p}_slot2_end", (-1, -1)), base["slot2_end"])
            )

        converged, after = await wait_for_expected_state(reader, _ok)
        flags_ok = sent_flags == expected_flags
        _log_event(
            "test_check",
            test=label,
            sent_flags=f"0x{sent_flags:02X}",
            expected_flags=f"0x{expected_flags:02X}",
            converged=converged,
            passed=flags_ok and converged,
        )
        return flags_ok and converged, after

    return await attempt_with_retries(_action, label)


async def run_time_field_case(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    schedule: str,
    state: dict,
    enabled_combo: tuple[bool, bool],
    field: str,
) -> tuple[bool, dict | None]:
    p = schedule
    s1_enabled, s2_enabled = enabled_combo
    base = _snapshot_schedule(state, p)

    # Build expected with one-field change (as HA UI time entity does).
    expected = dict(base)
    if field == "slot1_start":
        expected[field] = _next_time_pair(base[field], 1, 17)
    elif field == "slot1_end":
        expected[field] = _next_time_pair(base[field], 1, 33)
    elif field == "slot2_start":
        expected[field] = _next_time_pair(base[field], 2, 13)
    elif field == "slot2_end":
        expected[field] = _next_time_pair(base[field], 2, 23)
    else:
        raise ValueError(f"Unsupported field: {field}")

    expected_flags = EXPECTED_TIME_FLAGS[(s1_enabled, s2_enabled)]
    label = f"{schedule} time {_fmt_combo(s1_enabled, s2_enabled)} field={field}"

    async def _action(_attempt: int) -> tuple[bool, dict | None]:
        sent_flags = await send_schedule_command(
            reader,
            writer,
            schedule_type=schedule,
            slot1_start=expected["slot1_start"],
            slot1_end=expected["slot1_end"],
            slot2_start=expected["slot2_start"],
            slot2_end=expected["slot2_end"],
            slot1_enabled=s1_enabled,
            slot2_enabled=s2_enabled,
            write_mode="time",
            description=label,
        )

        def _ok(st: dict) -> bool:
            return (
                st.get(f"{p}_slot1_enabled") is s1_enabled
                and st.get(f"{p}_slot2_enabled") is s2_enabled
                and _times_equal(st.get(f"{p}_slot1_start", (-1, -1)), expected["slot1_start"])
                and _times_equal(st.get(f"{p}_slot1_end", (-1, -1)), expected["slot1_end"])
                and _times_equal(st.get(f"{p}_slot2_start", (-1, -1)), expected["slot2_start"])
                and _times_equal(st.get(f"{p}_slot2_end", (-1, -1)), expected["slot2_end"])
            )

        converged, after = await wait_for_expected_state(reader, _ok)
        flags_ok = sent_flags == expected_flags
        _log_event(
            "test_check",
            test=label,
            sent_flags=f"0x{sent_flags:02X}",
            expected_flags=f"0x{expected_flags:02X}",
            converged=converged,
            passed=flags_ok and converged,
        )
        return flags_ok and converged, after

    return await attempt_with_retries(_action, label)


async def restore_schedule(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    schedule: str,
    original: dict,
) -> tuple[bool, dict | None]:
    p = schedule
    label = f"restore {schedule}"

    async def _action(_attempt: int) -> tuple[bool, dict | None]:
        # First restore times with time mode (uses capture-proven time flags).
        _ = await send_schedule_command(
            reader,
            writer,
            schedule_type=schedule,
            slot1_start=original["slot1_start"],
            slot1_end=original["slot1_end"],
            slot2_start=original["slot2_start"],
            slot2_end=original["slot2_end"],
            slot1_enabled=original["slot1_enabled"],
            slot2_enabled=original["slot2_enabled"],
            write_mode="time",
            description=f"{label} (time)",
        )

        # Then enforce enable bits via state mode.
        _ = await send_schedule_command(
            reader,
            writer,
            schedule_type=schedule,
            slot1_start=original["slot1_start"],
            slot1_end=original["slot1_end"],
            slot2_start=original["slot2_start"],
            slot2_end=original["slot2_end"],
            slot1_enabled=original["slot1_enabled"],
            slot2_enabled=original["slot2_enabled"],
            write_mode="state",
            description=f"{label} (state)",
        )

        def _ok(st: dict) -> bool:
            return (
                st.get(f"{p}_slot1_enabled") is original["slot1_enabled"]
                and st.get(f"{p}_slot2_enabled") is original["slot2_enabled"]
                and _times_equal(st.get(f"{p}_slot1_start", (-1, -1)), original["slot1_start"])
                and _times_equal(st.get(f"{p}_slot1_end", (-1, -1)), original["slot1_end"])
                and _times_equal(st.get(f"{p}_slot2_start", (-1, -1)), original["slot2_start"])
                and _times_equal(st.get(f"{p}_slot2_end", (-1, -1)), original["slot2_end"])
            )

        converged, after = await wait_for_expected_state(reader, _ok)
        return converged, after

    return await attempt_with_retries(_action, label)


async def run() -> None:
    global _log_file, _raw_bin_file

    print("\n" + "=" * 78)
    print("  Schedule Test — reliable HA-UI combinations")
    print("=" * 78)
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Log:  {LOG_PATH}")
    print(f"  Raw:  {RAW_BIN_PATH}")
    print("=" * 78)

    if not HOST:
        print("ERROR: SPA_BRIDGE_HOST not set in .env")
        return

    input("\nPress ENTER to connect and run...")

    _log_file = open(LOG_PATH, "a")
    _raw_bin_file = open(RAW_BIN_PATH, "ab")
    _log_event("session_start", host=HOST, port=PORT)

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    reader, writer = await asyncio.open_connection(HOST, PORT)

    results: list[tuple[str, bool]] = []
    state: dict | None = None
    originals: dict[str, dict] = {}

    try:
        await drain_stale(reader)
        state = await read_broadcast(reader)
        if state is None:
            print("No valid broadcast received; aborting.")
            return

        originals["heat"] = _snapshot_schedule(state, "heat")
        originals["filter"] = _snapshot_schedule(state, "filter")

        print("\nBaseline:")
        print(f"  Heat:   {_format_schedule(state, 'heat')}")
        print(f"  Filter: {_format_schedule(state, 'filter')}")
        _log_event(
            "baseline",
            heat=_format_schedule(state, "heat"),
            filter=_format_schedule(state, "filter"),
        )

        for schedule in ("heat", "filter"):
            print(f"\n--- {schedule.capitalize()} ---")

            # 1) State mode: verify all UI-reachable combinations.
            for s1, s2 in COMBOS:
                name = f"{schedule.capitalize()}: state {_fmt_combo(s1, s2)}"
                ok, new_state = await ensure_enable_combo(reader, writer, schedule, state, s1, s2)
                results.append((name, ok))
                print(f"  {'PASS' if ok else 'FAIL'} {name}")
                if new_state is not None:
                    state = new_state

            # 2) Time mode: for each combination, test each single-field edit.
            for s1, s2 in COMBOS:
                prep_name = f"{schedule.capitalize()}: prepare {_fmt_combo(s1, s2)}"
                ok, new_state = await ensure_enable_combo(reader, writer, schedule, state, s1, s2)
                results.append((prep_name, ok))
                print(f"  {'PASS' if ok else 'FAIL'} {prep_name}")
                if new_state is not None:
                    state = new_state
                if not ok:
                    # Skip field tests for this combo if preparation failed.
                    for field in TIME_FIELDS:
                        skip_name = f"{schedule.capitalize()}: time {_fmt_combo(s1, s2)} field={field} (skipped)"
                        results.append((skip_name, False))
                        print(f"  FAIL {skip_name}")
                    continue

                for field in TIME_FIELDS:
                    name = f"{schedule.capitalize()}: time {_fmt_combo(s1, s2)} field={field}"
                    ok, new_state = await run_time_field_case(
                        reader,
                        writer,
                        schedule,
                        state,
                        (s1, s2),
                        field,
                    )
                    results.append((name, ok))
                    print(f"  {'PASS' if ok else 'FAIL'} {name}")
                    if new_state is not None:
                        state = new_state

        # Restore original schedules robustly.
        print("\nRestoring original schedules...")
        for schedule in ("heat", "filter"):
            ok, new_state = await restore_schedule(reader, writer, schedule, originals[schedule])
            results.append((f"Restore {schedule}", ok))
            print(f"  {'PASS' if ok else 'FAIL'} Restore {schedule}")
            if new_state is not None:
                state = new_state

        if state is not None:
            print(f"  Restored heat:   {_format_schedule(state, 'heat')}")
            print(f"  Restored filter: {_format_schedule(state, 'filter')}")

    finally:
        writer.close()

        passed = sum(1 for _, ok in results if ok)
        failed = sum(1 for _, ok in results if not ok)

        print("\n" + "=" * 78)
        print("RESULTS")
        print("=" * 78)
        for name, ok in results:
            print(f"  {'PASS' if ok else 'FAIL'} {name}")
        print(f"\nTotal: {passed} passed, {failed} failed")
        print("=" * 78)

        _log_event(
            "session_end",
            total=len(results),
            passed=passed,
            failed=failed,
            results=[{"test": n, "result": "pass" if ok else "fail"} for n, ok in results],
        )

        if _log_file:
            _log_file.close()
        if _raw_bin_file:
            _raw_bin_file.close()

        print(f"Log saved: {LOG_PATH}")
        print(f"Raw saved: {RAW_BIN_PATH}")


if __name__ == "__main__":
    asyncio.run(run())



