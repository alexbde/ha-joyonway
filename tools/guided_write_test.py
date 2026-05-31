"""Guided live write-test script for Joyonway P25B85.

Run this at the spa with a direct TCP connection to the EW11 bridge.
It sends write commands one by one, reads the broadcast to verify state,
and asks for physical confirmation between tests.

All broadcasts and commands are captured to a JSONL log file for
post-hoc analysis (saved to tools/captures_write_test/).

Usage:
    source .venv/bin/activate
    python tools/guided_write_test.py

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

# Import directly from source files to avoid __init__.py (which imports HA)
from importlib.util import module_from_spec, spec_from_file_location
import types

_comp_dir = Path(__file__).resolve().parent.parent / "custom_components" / "joyonway"


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

from joyonway.adapters.p25b85 import P25B85Adapter  # noqa: E402
from joyonway.protocol import (  # noqa: E402
    find_frames,
    is_broadcast,
    unescape_frame,
    validate_frame,
)

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
BROADCAST_TIMEOUT = 5.0  # seconds to wait for a valid broadcast
POST_COMMAND_DELAY = 2.5  # seconds to wait after sending before reading
# The broadcast interval is ~2s, so 2.5s ensures the controller has time
# to process the command and include the new state in the next broadcast.

adapter = P25B85Adapter()

# ─── Capture log ─────────────────────────────────────────────────
# Every broadcast parse and command send is recorded to a JSONL file
# so issues spotted after a test session can be analyzed offline.

CAPTURE_DIR = Path(__file__).resolve().parent / "captures_write_test"
CAPTURE_DIR.mkdir(exist_ok=True)

_log_path: Path | None = None
_log_file = None  # file handle, opened lazily


def _init_capture_log() -> Path:
    """Create a timestamped JSONL capture log and return its path."""
    global _log_path, _log_file
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _log_path = CAPTURE_DIR / f"write_test_{ts}.jsonl"
    _log_file = open(_log_path, "a")
    return _log_path


def _log_event(event_type: str, **kwargs) -> None:
    """Append one JSON line to the capture log."""
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


def _close_capture_log() -> None:
    """Flush and close the capture log."""
    global _log_file
    if _log_file is not None:
        _log_file.close()
        _log_file = None


# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}✅ PASS:{RESET} {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}❌ FAIL:{RESET} {msg}")


def info(msg: str) -> None:
    print(f"  {CYAN}ℹ️  {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}⚠️  {msg}{RESET}")


class UserQuit(Exception):
    """Raised when user wants to exit the script."""


def ask(prompt: str) -> bool:
    """Ask user for confirmation. Returns True if confirmed.

    Type 'q' or 'quit' to abort the entire script.
    """
    resp = input(f"\n  {BOLD}>>> {prompt} [y/N/q]: {RESET}").strip().lower()
    if resp in ("q", "quit", "exit"):
        raise UserQuit()
    return resp in ("y", "yes")


def wait_enter(prompt: str) -> None:
    """Wait for user to press ENTER. Type 'q' to quit."""
    resp = input(f"\n  {BOLD}>>> {prompt} [ENTER/q]: {RESET}").strip().lower()
    if resp in ("q", "quit", "exit"):
        raise UserQuit()


def confirm(prompt: str) -> bool:
    """Ask user to confirm a physical observation. Returns True if confirmed.

    y/ENTER = yes (pass), n = no (fail), q = quit script.
    """
    resp = input(f"\n  {BOLD}>>> {prompt} [Y/n/q]: {RESET}").strip().lower()
    if resp in ("q", "quit", "exit"):
        raise UserQuit()
    if resp in ("n", "no"):
        return False
    return True  # ENTER or y


async def drain_stale(reader: asyncio.StreamReader) -> None:
    """Drain any buffered data from the TCP socket so next read gets fresh data."""
    while True:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), timeout=0.05)
            if not chunk:
                break
        except asyncio.TimeoutError:
            break


async def read_broadcast(
    reader: asyncio.StreamReader, drain_first: bool = False
) -> dict | None:
    """Read from TCP stream until we get a valid P25B85 broadcast frame.

    Uses the same frame-parsing pipeline as the real coordinator:
      find_frames() → validate_frame() → is_broadcast() →
      unescape_frame() → adapter.parse_status()

    If drain_first is True, discard all currently buffered data before waiting
    for a fresh frame.  This is critical after sending a command: without it
    we would read a stale pre-command broadcast that was buffered during the
    POST_COMMAND_DELAY sleep.

    Returns the LAST valid broadcast from all buffered data (not the first).
    """
    if drain_first:
        await drain_stale(reader)

    deadline = time.monotonic() + BROADCAST_TIMEOUT
    buf = bytearray()
    latest_result: dict | None = None

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

        # Use the same frame-finding logic as the real coordinator
        raw_frames = find_frames(bytes(buf))
        if not raw_frames:
            continue

        # Advance buffer past the last frame we found
        last_end = bytes(buf).rfind(b"\x1d")
        if last_end >= 0:
            buf = buf[last_end + 1:]

        for raw_frame in raw_frames:
            if not validate_frame(raw_frame):
                continue
            if not is_broadcast(raw_frame):
                continue

            try:
                logical = unescape_frame(
                    raw_frame, full=adapter.unescape_full_frame
                )
                result = adapter.parse_status(logical)
                if result is not None:
                    _log_broadcast(raw_frame, logical, result)
                    latest_result = result
            except Exception:
                continue

        # If we have a valid result and buffer is empty, we're done.
        if latest_result is not None and not buf:
            break

    return latest_result


def _log_broadcast(raw: bytes, logical: bytes, parsed: dict) -> None:
    """Log a parsed broadcast frame to the capture log."""
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
    _log_event(
        "broadcast",
        raw_hex=raw.hex(),
        logical_hex=logical.hex(),
        parsed=safe,
    )


async def send_command(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    cmd: bytes,
    description: str,
) -> None:
    """Send a command frame to the bridge.

    Drains stale buffered broadcasts BEFORE sending so that the next
    read_broadcast() call gets a fresh post-command frame.
    """
    await drain_stale(reader)
    info(f"Sending: {description}")
    _log_event("command", description=description, cmd_hex=cmd.hex())
    writer.write(cmd)
    await writer.drain()
    await asyncio.sleep(POST_COMMAND_DELAY)


async def run_tests() -> None:
    """Run the guided test sequence.

    Each test is a round-trip:
      1. Read current state
      2. Send command to change state
      3. Verify new state in broadcast + physical confirmation
      4. Send command to restore original state
      5. Verify restored state
    The spa ends in the same state it started.
    """
    print(f"\n{'='*60}")
    print(f"  {BOLD}Guided Live Write Test — Joyonway P25B85{RESET}")
    print(f"{'='*60}")
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Each test is a round-trip: change state, verify,")
    print(f"  then restore original state. Spa ends unchanged.")
    print(f"{'='*60}\n")

    if not HOST:
        print(f"{RED}ERROR: SPA_BRIDGE_HOST not set in .env{RESET}")
        return

    # ─── TEST MENU ────────────────────────────────────────────────
    ALL_TESTS = [
        ("light", "Light (toggle on/off)"),
        ("heater", "Heater (on/off)"),
        ("blower", "Blower (on/off)"),
        ("jets", "Jets (full pump cycle)"),
        ("temperature", "Temperature setpoint (dynamic frame testing variants)"),
        ("heat_schedule", "Heat schedule (write + restore)"),
        ("filter_schedule", "Filter schedule (hours, minutes, enable/disable)"),
        ("clock", "Clock write (verify Y/M/D H:m:s fields)"),
    ]

    print(f"  {BOLD}Select tests to run:{RESET}\n")
    print(f"    {BOLD}0{RESET} — Run ALL tests")
    for i, (_, label) in enumerate(ALL_TESTS, 1):
        print(f"    {BOLD}{i}{RESET} — {label}")
    print()

    selection = input(f"  {BOLD}Enter choice (0-{len(ALL_TESTS)}, or comma-separated e.g. 1,3,5): {RESET}").strip()

    if selection == "0" or selection == "":
        selected = {key for key, _ in ALL_TESTS}
    else:
        selected = set()
        for part in selection.split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(ALL_TESTS):
                    selected.add(ALL_TESTS[idx][0])

    if not selected:
        print(f"{RED}No valid tests selected.{RESET}")
        return

    selected_names = [label for key, label in ALL_TESTS if key in selected]
    info(f"Running {len(selected_names)} test(s): {', '.join(selected_names)}")

    # ─── CAPTURE LOG ─────────────────────────────────────────────
    log_path = _init_capture_log()
    _log_event("session_start", host=HOST, port=PORT, tests=sorted(selected))
    info(f"Capture log: {log_path}")

    # ─── CONNECT ──────────────────────────────────────────────────
    info("Connecting to EW11 bridge...")
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        print(f"{RED}ERROR: Cannot connect: {e}{RESET}")
        return
    ok("Connected")

    results: list[tuple[str, bool | None]] = []

    try:
        await _run_test_sequence(reader, writer, selected, adapter, results)
    except (UserQuit, KeyboardInterrupt):
        print(f"\n\n  {YELLOW}⏹️  Aborted by user.{RESET}")
    finally:
        # Log results before closing
        for name, result in results:
            _log_event("test_result", test=name,
                       result="pass" if result is True else "fail" if result is False else "skipped")
        _log_event("session_end")
        _close_capture_log()
        writer.close()
        _print_summary(results)
        if _log_path:
            info(f"Capture log saved: {_log_path}")


async def _run_test_sequence(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    selected: set[str],
    adapter: P25B85Adapter,
    results: list[tuple[str, bool | None]],
) -> None:
    """Run the selected tests. Separated so UserQuit/Ctrl+C are caught cleanly."""

    # Initial state read
    info("Reading initial broadcast state...")
    state = await read_broadcast(reader)
    if state is None:
        fail("No valid broadcast received within timeout")
        return
    ok(f"Initial state: status={state.get('status')}, jets={state.get('jets')}, "
       f"light={state.get('light')}, blower={state.get('blower')}, "
       f"water={state.get('water_temperature')}°C, setpoint={state.get('setpoint')}°C, "
        f"heater_byte=0x{state.get('heater_byte', 0):02X}")

    # ─── HELPER ──────────────────────────────────────────────────

    def log_heater_state(label: str, st: dict) -> None:
        """Log detailed heater/status state for diagnostics."""
        hb = st.get("heater_byte", 0)
        info(f"{label}: status={st.get('status')!r}, heater_byte=0x{hb:02X}, "
             f"heater_active={st.get('heater_active')}, ozone_active={st.get('ozone_active')}, "
             f"blower={st.get('blower')}")

    async def round_trip_toggle(
        test_name: str,
        field: str,
        cmd_on: bytes,
        cmd_off: bytes,
        check_on,
        check_off,
        label_on: str = "ON",
        label_off: str = "OFF",
    ) -> None:
        """Run a round-trip toggle test.

        Reads current state, toggles to opposite, verifies, toggles back.
        """
        nonlocal state
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: {test_name}{RESET}")
        _log_event("test_start", test=test_name)

        current = state.get(field) if state else None
        is_on = check_on(current)
        info(f"Current {field} = {current} ({'ON' if is_on else 'OFF'})")

        if is_on:
            # Currently ON → test OFF then back to ON
            steps = [
                (cmd_off, label_off, check_off, f"Confirm {test_name} turned {label_off} on panel"),
                (cmd_on, label_on, check_on, f"Confirm {test_name} restored to {label_on} on panel"),
            ]
        else:
            # Currently OFF → test ON then back to OFF
            steps = [
                (cmd_on, label_on, check_on, f"Confirm {test_name} turned {label_on} on panel"),
                (cmd_off, label_off, check_off, f"Confirm {test_name} restored to {label_off} on panel"),
            ]

        if not ask(f"Run {test_name} round-trip test?"):
            results.append((test_name, None))
            return

        step_pass = True
        for cmd, label, checker, confirm_msg in steps:
            await send_command(reader, writer, cmd, f"{test_name} → {label}")
            new_state = await read_broadcast(reader)
            if new_state:
                state = new_state
                val = new_state.get(field)
                if checker(val):
                    ok(f"Broadcast confirms {field} → {label} ({val})")
                else:
                    fail(f"Expected {label}, got {field}={val}")
                    step_pass = False
            else:
                fail("No broadcast received")
                step_pass = False
            if not confirm(confirm_msg):
                fail(f"User reports {test_name} did NOT change to {label} on panel")
                step_pass = False

        results.append((test_name, step_pass))

    # ─── TEST 1: Light ───────────────────────────────────────────

    if "light" in selected:
        await round_trip_toggle(
            test_name="Light",
            field="light",
            cmd_on=adapter.build_light_toggle_command(),
            cmd_off=adapter.build_light_toggle_command(),
            check_on=lambda v: v is True,
            check_off=lambda v: v is False,
        )

    # ─── TEST 2: Heater (extended diagnostics) ─────────────────

    if "heater" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Heater (extended — captures raw bytes + transitions){RESET}")
        _log_event("test_start", test="Heater")

        current_status = state.get("status") if state else None
        log_heater_state("Before", state)
        heater_is_on = current_status in ("circulation", "heating")

        if not ask("Run heater round-trip test?"):
            results.append(("Heater", None))
        else:
            heater_pass = True

            # Decide which direction to test first
            if heater_is_on:
                steps = [
                    (adapter.build_heater_command(on=False), "OFF"),
                    (adapter.build_heater_command(on=True), "ON (restore)"),
                ]
            else:
                steps = [
                    (adapter.build_heater_command(on=True), "ON"),
                    (adapter.build_heater_command(on=False), "OFF (restore)"),
                ]

            for cmd, label in steps:
                await send_command(reader, writer, cmd, f"Heater → {label}")

                # Read multiple broadcasts to capture state transitions
                # (heater typically goes off → circulation → heating over 2-5s)
                info(f"Monitoring heater state transitions (up to 10s)...")
                seen_states: list[tuple[str, int]] = []
                t_end = time.monotonic() + 10.0
                last_status = None
                while time.monotonic() < t_end:
                    new_state = await read_broadcast(reader)
                    if new_state:
                        state = new_state
                        hb = new_state.get("heater_byte", 0)
                        st = new_state.get("status", "?")
                        if st != last_status:
                            seen_states.append((st, hb))
                            last_status = st
                            info(f"  → status={st!r}  heater_byte=0x{hb:02X}  "
                                 f"heater_active={new_state.get('heater_active')}  "
                                 f"ozone_active={new_state.get('ozone_active')}")
                        # Stop early once we reach the target state
                        if "ON" in label and st in ("circulation", "heating"):
                            break
                        if "OFF" in label and st == "off":
                            break

                final_status = state.get("status") if state else None
                final_hb = state.get("heater_byte", 0) if state else 0

                if "ON" in label:
                    if final_status in ("circulation", "heating"):
                        ok(f"Heater {label} confirmed: status={final_status!r}, "
                           f"heater_byte=0x{final_hb:02X}")
                    else:
                        fail(f"Heater {label} NOT confirmed: status={final_status!r}, "
                             f"heater_byte=0x{final_hb:02X}")
                        heater_pass = False
                else:
                    if final_status == "off":
                        ok(f"Heater {label} confirmed: status={final_status!r}, "
                           f"heater_byte=0x{final_hb:02X}")
                    else:
                        fail(f"Heater {label} NOT confirmed: status={final_status!r}, "
                             f"heater_byte=0x{final_hb:02X}")
                        heater_pass = False

                # Flag unknown states
                if final_status == "unknown":
                    warn(f"Status is 'unknown' — heater_byte=0x{final_hb:02X} is not "
                         f"in HEATER_STATE_MAP. This will show as 'unknown' in the UI!")

                if seen_states:
                    info(f"Transition sequence: {' → '.join(f'{s}(0x{hb:02X})' for s, hb in seen_states)}")

                if not confirm(f"Confirm heater is {label} on panel?"):
                    fail(f"User reports heater did NOT change to {label}")
                    heater_pass = False

            results.append(("Heater", heater_pass))

    # ─── TEST 3: Blower (with min-run-time handling) ────────────

    if "blower" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Blower (ON/OFF with extended OFF delay){RESET}")
        _log_event("test_start", test="Blower")

        current_blower = state.get("blower", False) if state else False
        info(f"Current blower = {current_blower}")
        info("Note: blower may have a minimum run time. OFF will be "
             "tested after a 10s delay.")

        if not ask("Run blower round-trip test?"):
            results.append(("Blower", None))
        else:
            blower_pass = True

            if current_blower:
                # Already on → test OFF first, then restore ON
                steps = [
                    (adapter.build_blower_command(on=False), False, "OFF", 0),
                    (adapter.build_blower_command(on=True), True, "ON (restore)", 0),
                ]
            else:
                # Currently off → turn ON, wait, then OFF
                steps = [
                    (adapter.build_blower_command(on=True), True, "ON", 10),
                    (adapter.build_blower_command(on=False), False, "OFF (restore)", 0),
                ]

            for cmd, expected_val, label, extra_wait in steps:
                await send_command(reader, writer, cmd, f"Blower → {label}")
                if extra_wait > 0:
                    info(f"Waiting {extra_wait}s for blower min-run-time...")
                    await asyncio.sleep(extra_wait)
                    await drain_stale(reader)
                state = await read_broadcast(reader)
                if state:
                    val = state.get("blower")
                    hb = state.get("heater_byte", 0)
                    if val == expected_val:
                        ok(f"Broadcast confirms blower={val} "
                           f"(heater_byte=0x{hb:02X})")
                    else:
                        fail(f"Expected blower={expected_val}, got {val} "
                             f"(heater_byte=0x{hb:02X})")
                        blower_pass = False
                else:
                    fail("No broadcast received")
                    blower_pass = False
                if not confirm(f"Confirm blower is {label} on panel?"):
                    fail(f"User reports blower did NOT change to {label}")
                    blower_pass = False

            results.append(("Blower", blower_pass))

    # ─── TEST 4: Jets (pump cycle with retry) ───────────────────

    if "jets" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Jets (full cycle with retry){RESET}")
        _log_event("test_start", test="Jets cycle")
        current_jets = state.get("jets", "off") if state else "off"
        info(f"Current jets = {current_jets}")
        info("Each transition retries up to 3× to handle RS485 bus collisions.")

        if not ask("Run jets full-cycle round-trip test?"):
            results.append(("Jets cycle", None))
        else:
            # Define the cycle based on current state to end where we started
            if current_jets == "off":
                cycle = [
                    ("off", "low", "Confirm pump LOW on panel"),
                    ("low", "high", "Confirm pump HIGH on panel"),
                    ("high", "off", "Confirm pump OFF (restored) on panel"),
                ]
            elif current_jets == "low":
                cycle = [
                    ("low", "high", "Confirm pump HIGH on panel"),
                    ("high", "off", "Confirm pump OFF on panel"),
                    ("off", "low", "Confirm pump LOW (restored) on panel"),
                ]
            else:  # high
                cycle = [
                    ("high", "off", "Confirm pump OFF on panel"),
                    ("off", "low", "Confirm pump LOW on panel"),
                    ("low", "high", "Confirm pump HIGH (restored) on panel"),
                ]

            jets_pass = True
            actual_jets = current_jets
            for from_state, expected, confirm_msg in cycle:
                cmd = adapter.build_pump_command(expected)
                if cmd is None:
                    fail(f"No pump command for target '{expected}'")
                    jets_pass = False
                    break

                # Retry up to 3 times — RS485 bus collisions can cause
                # commands to be lost (our frame sent while controller
                # is mid-broadcast → both garbled on the wire).
                MAX_RETRIES = 3
                succeeded = False
                for attempt in range(1, MAX_RETRIES + 1):
                    if attempt > 1:
                        warn(f"Retry {attempt}/{MAX_RETRIES} for {actual_jets}→{expected}...")
                        await asyncio.sleep(1.0)  # extra gap before retry
                    await send_command(reader, writer, cmd, f"Jets → {expected} (attempt {attempt})")
                    state = await read_broadcast(reader)
                    if state and state.get("jets") == expected:
                        ok(f"Broadcast confirms jets={expected}"
                           + (f" (took {attempt} attempt(s))" if attempt > 1 else ""))
                        actual_jets = expected
                        succeeded = True
                        break
                    else:
                        got = state.get('jets') if state else 'N/A'
                        if attempt < MAX_RETRIES:
                            warn(f"jets={got} (expected {expected}), retrying...")
                        else:
                            fail(f"jets={got} after {MAX_RETRIES} attempts "
                                 f"(expected {expected})")

                if not succeeded:
                    # Update actual_jets from latest broadcast for next step
                    if state:
                        actual_jets = state.get("jets", actual_jets)
                if not confirm(confirm_msg):
                    fail(f"User reports jets did NOT change to {expected}")
                    jets_pass = False

            results.append(("Jets cycle", jets_pass))

    # ─── TEST 4b: Temperature Setpoint ───────────────────────────

    if "temperature" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Temperature setpoint (via adapter.build_temp_command){RESET}")
        _log_event("test_start", test="Temperature setpoint")

        orig_setpoint_c = state.get('setpoint')

        if not orig_setpoint_c:
            fail("Could not read original setpoint from broadcast.")
            results.append(("Temperature setpoint", False))
        else:
            info(f"Current setpoint: {orig_setpoint_c}°C")
            
            # Decide on a test target (+1 deg, unless it's >39, then -1 deg)
            test_c = orig_setpoint_c + 1 if orig_setpoint_c < 39 else orig_setpoint_c - 1
            
            if not ask(f"Test temperature setpoint command (targeting {test_c}°C)?"):
                results.append(("Temperature setpoint", None))
            else:
                temp_pass = False

                # Use the adapter's build_temp_command (btn_action=0x98, confirmed working)
                frame = adapter.build_temp_command(test_c)
                if frame is None:
                    fail(f"build_temp_command returned None for {test_c}°C")
                else:
                    await send_command(reader, writer, frame, f"TEMP SETPOINT → {test_c}°C")
                    state = await read_broadcast(reader)
                    
                    if state and state.get('setpoint') == test_c:
                        ok(f"Setpoint updated to {test_c}°C!")
                        temp_pass = True
                    else:
                        actual = state.get('setpoint') if state else None
                        fail(f"Setpoint failed. Expected {test_c}°C, got {actual}°C")

                if temp_pass:
                    if not confirm(f"Confirm panel shows new target {test_c}°C?"):
                        fail("User reports panel did NOT update setpoint")
                        temp_pass = False
                    # Restore original
                    frame = adapter.build_temp_command(orig_setpoint_c)
                    await send_command(reader, writer, frame, f"TEMP SETPOINT → restoring {orig_setpoint_c}°C")
                    state = await read_broadcast(reader)
                    if state and state.get('setpoint') == orig_setpoint_c:
                        ok(f"Restored setpoint to {orig_setpoint_c}°C!")
                    else:
                        warn("Failed to automatically confirm restore in broadcast.")
                        if not confirm(f"Manually verify panel restored to {orig_setpoint_c}°C?"):
                            warn("User reports restore failed")
                
                results.append(("Temperature setpoint", temp_pass))

    # ─── TEST 5: Heat schedule ────────────────────────────────────

    if "heat_schedule" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Heat schedule write + restore{RESET}")
        _log_event("test_start", test="Heat schedule")

        orig_hs1 = state.get("heat_slot1_start", (0, 0)) if state else (0, 0)
        orig_he1 = state.get("heat_slot1_end", (0, 0)) if state else (0, 0)
        orig_hs2 = state.get("heat_slot2_start", (0, 0)) if state else (0, 0)
        orig_he2 = state.get("heat_slot2_end", (0, 0)) if state else (0, 0)
        orig_s1_en = state.get("heat_slot1_enabled", True) if state else True
        orig_s2_en = state.get("heat_slot2_enabled", True) if state else True
        info(f"Current: slot1={orig_hs1[0]:02d}:{orig_hs1[1]:02d}-{orig_he1[0]:02d}:{orig_he1[1]:02d} "
             f"({'ON' if orig_s1_en else 'OFF'}), "
             f"slot2={orig_hs2[0]:02d}:{orig_hs2[1]:02d}-{orig_he2[0]:02d}:{orig_he2[1]:02d} "
             f"({'ON' if orig_s2_en else 'OFF'})")

        # Compute test values that DIFFER from current state (shift hours by +2, toggle minutes)
        test_hs1 = ((orig_hs1[0] + 2) % 24, 15 if orig_hs1[1] != 15 else 45)
        test_he1 = ((orig_he1[0] + 2) % 24, 30 if orig_he1[1] != 30 else 0)
        test_hs2 = ((orig_hs2[0] + 2) % 24, 15 if orig_hs2[1] != 15 else 45)
        test_he2 = ((orig_he2[0] + 2) % 24, 30 if orig_he2[1] != 30 else 0)
        test_s1_en = not orig_s1_en  # toggle enable state
        test_s2_en = not orig_s2_en

        info(f"Test values (all different from current):")
        info(f"  slot1={test_hs1[0]:02d}:{test_hs1[1]:02d}-{test_he1[0]:02d}:{test_he1[1]:02d} "
             f"({'ON' if test_s1_en else 'OFF'}), "
             f"slot2={test_hs2[0]:02d}:{test_hs2[1]:02d}-{test_he2[0]:02d}:{test_he2[1]:02d} "
             f"({'ON' if test_s2_en else 'OFF'})")

        if not ask(f"Write test schedule, then restore?"):
            results.append(("Heat schedule", None))
        else:
            frame = adapter.build_schedule_command(
                "heat", test_hs1, test_he1, test_hs2, test_he2,
                slot1_enabled=test_s1_en, slot2_enabled=test_s2_en,
            )
            await send_command(reader, writer, frame, "HEAT_SCHEDULE → test values")
            state = await read_broadcast(reader)
            sched_pass = False
            if state:
                s1 = state.get("heat_slot1_start", (0, 0))
                e1 = state.get("heat_slot1_end", (0, 0))
                s2 = state.get("heat_slot2_start", (0, 0))
                e2 = state.get("heat_slot2_end", (0, 0))
                s1_en = state.get("heat_slot1_enabled")
                s2_en = state.get("heat_slot2_enabled")
                # Check each field
                checks = [
                    (s1, test_hs1, "slot1 start"),
                    (e1, test_he1, "slot1 end"),
                    (s2, test_hs2, "slot2 start"),
                    (e2, test_he2, "slot2 end"),
                    (s1_en, test_s1_en, "slot1 enabled"),
                    (s2_en, test_s2_en, "slot2 enabled"),
                ]
                all_ok = True
                for actual, expected, field_name in checks:
                    if actual == expected:
                        ok(f"{field_name}: {actual}")
                    else:
                        fail(f"{field_name}: expected {expected}, got {actual}")
                        all_ok = False
                sched_pass = all_ok
            else:
                fail("No broadcast after schedule write")
            if not confirm("Panel shows new heat schedule?"):
                fail("User reports heat schedule did NOT update on panel")
                sched_pass = False

            info("Restoring original heat schedule...")
            frame = adapter.build_schedule_command(
                "heat", orig_hs1, orig_he1, orig_hs2, orig_he2,
                slot1_enabled=orig_s1_en, slot2_enabled=orig_s2_en,
            )
            await send_command(reader, writer, frame, "HEAT_SCHEDULE → restore original")
            state = await read_broadcast(reader)
            if state:
                s1 = state.get("heat_slot1_start", (0, 0))
                e1 = state.get("heat_slot1_end", (0, 0))
                if s1 == orig_hs1 and e1 == orig_he1:
                    ok("Original heat schedule restored")
                else:
                    warn(f"Restore may have failed: got {s1}, expected {orig_hs1}")
                    sched_pass = False
            if not confirm("Panel shows original heat schedule?"):
                fail("User reports heat schedule restore failed")
                sched_pass = False
            results.append(("Heat schedule", sched_pass))

    # ─── TEST 6: Filter schedule ─────────────────────────────────

    if "filter_schedule" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Filter schedule (all fields + enable/disable){RESET}")
        _log_event("test_start", test="Filter schedule")

        orig_fs1 = state.get("filter_slot1_start", (0, 0)) if state else (0, 0)
        orig_fe1 = state.get("filter_slot1_end", (0, 0)) if state else (0, 0)
        orig_fs2 = state.get("filter_slot2_start", (0, 0)) if state else (0, 0)
        orig_fe2 = state.get("filter_slot2_end", (0, 0)) if state else (0, 0)
        orig_s1_enabled = state.get("filter_slot1_enabled", False) if state else False
        orig_s2_enabled = state.get("filter_slot2_enabled", False) if state else False
        info(f"Current slot 1: {orig_fs1[0]:02d}:{orig_fs1[1]:02d}-{orig_fe1[0]:02d}:{orig_fe1[1]:02d} "
             f"({'ON' if orig_s1_enabled else 'OFF'})")
        info(f"Current slot 2: {orig_fs2[0]:02d}:{orig_fs2[1]:02d}-{orig_fe2[0]:02d}:{orig_fe2[1]:02d} "
             f"({'ON' if orig_s2_enabled else 'OFF'})")

        if not ask("Run filter schedule round-trip test (tests hours, minutes, enable/disable)?"):
            results.append(("Filter schedule", None))
        else:
            fsched_pass = True

            # Step 1: Compute test values that DIFFER from current state
            test_fs1 = ((orig_fs1[0] + 3) % 24, 15 if orig_fs1[1] != 15 else 45)
            test_fe1 = ((orig_fe1[0] + 3) % 24, 30 if orig_fe1[1] != 30 else 0)
            test_fs2 = ((orig_fs2[0] + 3) % 24, 15 if orig_fs2[1] != 15 else 45)
            test_fe2 = ((orig_fe2[0] + 3) % 24, 30 if orig_fe2[1] != 30 else 0)
            info(f"Test values (all different from current):")
            info(f"  slot1={test_fs1[0]:02d}:{test_fs1[1]:02d}-{test_fe1[0]:02d}:{test_fe1[1]:02d}, "
                 f"slot2={test_fs2[0]:02d}:{test_fs2[1]:02d}-{test_fe2[0]:02d}:{test_fe2[1]:02d} (both ON)")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=True, slot2_enabled=True,
            )
            await send_command(reader, writer, frame, "FILTER_SCHEDULE → test values (both enabled)")
            state = await read_broadcast(reader)
            if state:
                s1 = state.get("filter_slot1_start", (0, 0))
                e1 = state.get("filter_slot1_end", (0, 0))
                s2 = state.get("filter_slot2_start", (0, 0))
                e2 = state.get("filter_slot2_end", (0, 0))
                # Check each field individually for clear error messages
                checks = [
                    (s1[0], test_fs1[0], "slot1 start hour"),
                    (s1[1], test_fs1[1], "slot1 start minute"),
                    (e1[0], test_fe1[0], "slot1 end hour"),
                    (e1[1], test_fe1[1], "slot1 end minute"),
                    (s2[0], test_fs2[0], "slot2 start hour"),
                    (s2[1], test_fs2[1], "slot2 start minute"),
                    (e2[0], test_fe2[0], "slot2 end hour"),
                    (e2[1], test_fe2[1], "slot2 end minute"),
                ]
                for actual, expected, field_name in checks:
                    if actual == expected:
                        ok(f"{field_name}: {actual}")
                    else:
                        fail(f"{field_name}: expected {expected}, got {actual}")
                        fsched_pass = False

                # Check enabled flags — both should be ON
                s1_en = state.get("filter_slot1_enabled", None)
                s2_en = state.get("filter_slot2_enabled", None)
                if s1_en is True:
                    ok("Slot 1 enabled: True")
                else:
                    fail(f"Slot 1 enabled: expected True, got {s1_en}")
                    fsched_pass = False
                if s2_en is True:
                    ok("Slot 2 enabled: True")
                else:
                    fail(f"Slot 2 enabled: expected True, got {s2_en}")
                    fsched_pass = False
            else:
                fail("No broadcast after schedule write")
                fsched_pass = False
            if not confirm("Filter schedule shows new times on panel?"):
                fail("User reports filter schedule did NOT update")
                fsched_pass = False

            # Step 2: Disable slot 1 using flags byte (times stay the same)
            info("Testing slot disable: disabling slot 1 via flags byte (times unchanged)...")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=False, slot2_enabled=True,
            )
            await send_command(reader, writer, frame, "FILTER_SCHEDULE → slot1 disabled via flags")
            state = await read_broadcast(reader)
            if state:
                s1_en = state.get("filter_slot1_enabled", None)
                s2_en = state.get("filter_slot2_enabled", None)
                s1 = state.get("filter_slot1_start", (0, 0))
                if s1_en is False:
                    ok("Slot 1 disabled confirmed in broadcast")
                else:
                    fail(f"Slot 1 enabled flag = {s1_en} (expected False)")
                    fsched_pass = False
                if s2_en is True:
                    ok("Slot 2 still enabled")
                else:
                    warn(f"Slot 2 enabled flag = {s2_en} (expected True)")
                # Verify times are preserved despite disable
                if s1[0] == test_fs1[0] and s1[1] == test_fs1[1]:
                    ok(f"Slot 1 times preserved: {s1[0]:02d}:{s1[1]:02d}")
                else:
                    warn(f"Slot 1 times changed after disable: {s1}")
            else:
                fail("No broadcast")
                fsched_pass = False
            if not confirm("Filter slot 1 disabled on panel (times still visible)?"):
                fail("User reports slot 1 disable did NOT work")
                fsched_pass = False

            # Step 3: Re-enable slot 1
            info("Re-enabling slot 1...")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=True, slot2_enabled=True,
            )
            await send_command(reader, writer, frame, "FILTER_SCHEDULE → slot1 re-enabled")
            state = await read_broadcast(reader)
            if state:
                s1_en = state.get("filter_slot1_enabled", None)
                if s1_en is True:
                    ok("Slot 1 re-enabled confirmed")
                else:
                    fail(f"Slot 1 re-enable failed: {s1_en}")
                    fsched_pass = False
            else:
                fail("No broadcast")
                fsched_pass = False
            if not confirm("Filter slot 1 enabled again on panel?"):
                fail("User reports slot 1 re-enable did NOT work")
                fsched_pass = False

            # Step 4: Restore original
            info("Restoring original filter schedule...")
            frame = adapter.build_schedule_command(
                "filter", orig_fs1, orig_fe1, orig_fs2, orig_fe2,
                slot1_enabled=orig_s1_enabled, slot2_enabled=orig_s2_enabled,
            )
            await send_command(reader, writer, frame, "FILTER_SCHEDULE → restore original")
            state = await read_broadcast(reader)
            if state:
                s1 = state.get("filter_slot1_start", (0, 0))
                e1 = state.get("filter_slot1_end", (0, 0))
                if s1 == orig_fs1 and e1 == orig_fe1:
                    ok("Original filter schedule restored")
                else:
                    warn(f"Restore may have failed: got {s1}-{e1}, expected {orig_fs1}-{orig_fe1}")
                    fsched_pass = False
            if not confirm("Panel shows original filter schedule?"):
                fail("User reports filter schedule restore failed")
                fsched_pass = False
            results.append(("Filter schedule", fsched_pass))

    # ─── TEST 7: Clock (write time — date is read-only) ─────────

    if "clock" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Clock write (time only — date not settable via RS485){RESET}")
        _log_event("test_start", test="Clock write")
        info("Writes a known time, reads back H:M:S fields, then restores current time.")
        info("Note: controller ignores date bytes (Y/M/D) — only time is settable.")

        if not ask("Run clock write test?"):
            results.append(("Clock write", None))
        else:
            from datetime import datetime
            clock_pass = True

            # Write a distinctive test time with unique H:M:S values
            test_hour, test_minute, test_second = 14, 37, 52
            # Use current date (controller ignores date bytes anyway)
            now = datetime.now()
            info(f"Writing time: {test_hour:02d}:{test_minute:02d}:{test_second:02d}")

            frame = adapter.build_datetime_command(
                now.year, now.month, now.day, test_hour, test_minute, test_second
            )
            await send_command(reader, writer, frame, "DATETIME_SET → test time")
            state = await read_broadcast(reader)

            if state and state.get("spa_datetime"):
                spa_dt = state["spa_datetime"]
                info(f"Broadcast spa_datetime: {spa_dt}")

                # Only check time fields — date is not settable via 0xA2 command
                checks = [
                    (spa_dt.hour, test_hour, "hour"),
                    (spa_dt.minute, test_minute, "minute"),
                    # Second may drift by 1-2s due to broadcast delay
                    (abs(spa_dt.second - test_second) <= 3, True, "second (±3s)"),
                ]
                for actual, expected, field_name in checks:
                    if actual == expected:
                        ok(f"{field_name}: {actual}")
                    else:
                        fail(f"{field_name}: expected {expected}, got {actual}")
                        clock_pass = False

                # Informational: show what happened to date fields
                info(f"Date fields (read-only): {spa_dt.year}-{spa_dt.month:02d}-{spa_dt.day:02d}")
            else:
                fail("No spa_datetime in broadcast after write")
                clock_pass = False

            if not confirm(f"Panel shows time ~{test_hour:02d}:{test_minute:02d}?"):
                fail("User reports clock did NOT update")
                clock_pass = False

            # Restore: set current time
            info("Restoring current time...")
            now = datetime.now()
            frame = adapter.build_datetime_command(
                now.year, now.month, now.day, now.hour, now.minute, now.second
            )
            await send_command(reader, writer, frame, f"DATETIME_SET → restore ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            state = await read_broadcast(reader)
            if state and state.get("spa_datetime"):
                ok(f"Clock restored to: {state['spa_datetime']}")
            else:
                warn("Could not confirm clock restore")
            if not confirm("Panel clock shows current time?"):
                warn("User reports clock restore may have failed")
            results.append(("Clock write", clock_pass))

    # ─── SUMMARY ─────────────────────────────────────────────────


def _print_summary(results: list[tuple[str, bool | None]]) -> None:
    """Print test results summary."""
    print(f"\n{'='*60}")
    print(f"  {BOLD}TEST SUMMARY{RESET}")
    print(f"{'='*60}")
    if not results:
        print(f"  {YELLOW}No tests completed.{RESET}")
    else:
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

        print(f"\n  {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(run_tests())
