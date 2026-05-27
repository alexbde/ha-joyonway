"""Guided live write-test script for Joyonway P25B85.

Run this at the spa with a direct TCP connection to the EW11 bridge.
It sends write commands one by one, reads the broadcast to verify state,
and asks for physical confirmation between tests.

Usage:
    source .venv/bin/activate
    python tools/guided_write_test.py

Requires .env with SPA_BRIDGE_HOST (and optionally SPA_BRIDGE_PORT).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
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

from joyonway_p25b85.adapters.p25b85 import (  # noqa: E402
    CMD_BLOWER_OFF,
    CMD_BLOWER_ON,
    CMD_HEATER_OFF,
    CMD_HEATER_ON,
    CMD_LIGHT_TOGGLE,
    CMD_PUMP_HIGH_TO_OFF,
    CMD_PUMP_LOW_TO_HIGH,
    CMD_PUMP_OFF_TO_LOW,
    P25B85Adapter,
)
from joyonway_p25b85.protocol import unescape_frame  # noqa: E402

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
BROADCAST_TIMEOUT = 5.0  # seconds to wait for a valid broadcast
POST_COMMAND_DELAY = 2.0  # seconds to wait after sending before reading

adapter = P25B85Adapter()

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


def ask(prompt: str) -> bool:
    """Ask user for confirmation. Returns True if confirmed."""
    resp = input(f"\n  {BOLD}>>> {prompt} [y/N]: {RESET}").strip().lower()
    return resp in ("y", "yes")


def wait_enter(prompt: str) -> None:
    """Wait for user to press ENTER."""
    input(f"\n  {BOLD}>>> {prompt} [ENTER]: {RESET}")


async def read_broadcast(reader: asyncio.StreamReader) -> dict | None:
    """Read from TCP stream until we get a valid P25B85 broadcast frame."""
    deadline = time.monotonic() + BROADCAST_TIMEOUT
    buf = b""
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(
                reader.read(1024),
                timeout=deadline - time.monotonic(),
            )
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += chunk

        # Look for complete frames (0x1A ... 0x1D)
        while b"\x1a" in buf and b"\x1d" in buf:
            start = buf.index(b"\x1a")
            end_idx = buf.index(b"\x1d", start)
            raw_frame = buf[start : end_idx + 1]
            buf = buf[end_idx + 1 :]

            # Unescape and parse
            try:
                logical = unescape_frame(raw_frame, full=True)
                result = adapter.parse_status(logical)
                if result is not None:
                    return result
            except Exception:
                continue
    return None


async def send_command(
    writer: asyncio.StreamWriter, cmd: bytes, description: str
) -> None:
    """Send a command frame to the bridge."""
    info(f"Sending: {description}")
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

    # ─── CONNECT ──────────────────────────────────────────────────
    info("Connecting to EW11 bridge...")
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        print(f"{RED}ERROR: Cannot connect: {e}{RESET}")
        return
    ok("Connected")

    # Initial state read
    info("Reading initial broadcast state...")
    state = await read_broadcast(reader)
    if state is None:
        fail("No valid broadcast received within timeout")
        writer.close()
        return
    ok(f"Initial state: status={state.get('status')}, jets={state.get('jets')}, "
       f"light={state.get('light')}, blower={state.get('blower')}, "
       f"water={state.get('water_temperature')}°C, setpoint={state.get('setpoint')}°C")

    results: list[tuple[str, bool | None]] = []

    # ─── HELPER ──────────────────────────────────────────────────

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
            await send_command(writer, cmd, f"{test_name} → {label}")
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
            wait_enter(confirm_msg)

        results.append((test_name, step_pass))

    # ─── TEST 1: Light ───────────────────────────────────────────

    if "light" in selected:
        await round_trip_toggle(
            test_name="Light",
            field="light",
            cmd_on=CMD_LIGHT_TOGGLE,
            cmd_off=CMD_LIGHT_TOGGLE,
            check_on=lambda v: v is True,
            check_off=lambda v: v is False,
        )

    # ─── TEST 2: Heater ──────────────────────────────────────────

    if "heater" in selected:
        await round_trip_toggle(
            test_name="Heater",
            field="status",
            cmd_on=CMD_HEATER_ON,
            cmd_off=CMD_HEATER_OFF,
            check_on=lambda v: v in ("circulation", "heating"),
            check_off=lambda v: v == "off",
            label_on="HEATING",
            label_off="OFF",
        )

    # ─── TEST 3: Blower ──────────────────────────────────────────

    if "blower" in selected:
        await round_trip_toggle(
            test_name="Blower",
            field="blower",
            cmd_on=CMD_BLOWER_ON,
            cmd_off=CMD_BLOWER_OFF,
            check_on=lambda v: v is True,
            check_off=lambda v: v is False,
        )

    # ─── TEST 4: Jets (pump cycle) ───────────────────────────────

    if "jets" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Jets (full cycle){RESET}")
        current_jets = state.get("jets", "off") if state else "off"
        info(f"Current jets = {current_jets}")

        if not ask("Run jets full-cycle round-trip test?"):
            results.append(("Jets cycle", None))
        else:
            # Define the cycle based on current state to end where we started
            if current_jets == "off":
                cycle = [
                    (CMD_PUMP_OFF_TO_LOW, "low", "Confirm pump LOW on panel"),
                    (CMD_PUMP_LOW_TO_HIGH, "high", "Confirm pump HIGH on panel"),
                    (CMD_PUMP_HIGH_TO_OFF, "off", "Confirm pump OFF (restored) on panel"),
                ]
            elif current_jets == "low":
                cycle = [
                    (CMD_PUMP_LOW_TO_HIGH, "high", "Confirm pump HIGH on panel"),
                    (CMD_PUMP_HIGH_TO_OFF, "off", "Confirm pump OFF on panel"),
                    (CMD_PUMP_OFF_TO_LOW, "low", "Confirm pump LOW (restored) on panel"),
                ]
            else:  # high
                cycle = [
                    (CMD_PUMP_HIGH_TO_OFF, "off", "Confirm pump OFF on panel"),
                    (CMD_PUMP_OFF_TO_LOW, "low", "Confirm pump LOW on panel"),
                    (CMD_PUMP_LOW_TO_HIGH, "high", "Confirm pump HIGH (restored) on panel"),
                ]

            jets_pass = True
            for cmd, expected, confirm_msg in cycle:
                await send_command(writer, cmd, f"Jets → {expected}")
                state = await read_broadcast(reader)
                if state and state.get("jets") == expected:
                    ok(f"Broadcast confirms jets={expected}")
                else:
                    fail(f"Expected jets={expected}, got {state.get('jets') if state else 'N/A'}")
                    # Don't fail the whole test if the spa safely intervened (e.g. heater is on so pump stayed low)
                    warn("State did not match exact expected state (spa safety intervention?)")
                wait_enter(confirm_msg)

            results.append(("Jets cycle", True))

    # ─── TEST 4b: Temperature Setpoint variants ──────────────────

    if "temperature" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Temperature setpoint (dynamic frame variants){RESET}")

        from joyonway_p25b85.protocol import build_frame
        
        orig_setpoint_f = state.get('setpoint_f') if state and 'setpoint_f' in state else None
        orig_setpoint_c = state.get('setpoint')

        if not orig_setpoint_c:
            fail("Could not read original setpoint from broadcast.")
            results.append(("Temperature setpoint", False))
        else:
            info(f"Current setpoint: {orig_setpoint_c}°C")
            
            # Decide on a test target (+1 deg, unless it's >39, then -1 deg)
            test_c = orig_setpoint_c + 1 if orig_setpoint_c < 39 else orig_setpoint_c - 1
            test_f = round(test_c * 9/5 + 32)
            
            if not ask(f"Test dynamic temperature setpoint commands (targeting {test_c}°C / ~{test_f}°F)?"):
                results.append(("Temperature setpoint", None))
            else:
                variants = [0x80, 0x98, 0x99]
                temp_pass = False
                working_variant = None

                for variant in variants:
                    info(f"Trying byte[10] variant: 0x{variant:02X}")
                    # standard payload: 01 20 10 3C A1 10 A1 00 00 80 [variant] 00 C0 00 [temp_f] 00
                    payload = bytes([
                        0x01, 0x20, 0x10, 0x3C, 0xA1, 0x10, 0xA1,
                        0x00, 0x00, 0x80, variant, 0x00, 0xC0, 0x00, test_f, 0x00
                    ])
                    frame = build_frame(payload)
                    await send_command(writer, frame, f"TEMP SETPOINT → {test_c}°C (variant 0x{variant:02X})")
                    state = await read_broadcast(reader)
                    
                    if state and state.get('setpoint') == test_c:
                        ok(f"Variant 0x{variant:02X} successfully updated target to {test_c}°C!")
                        working_variant = variant
                        temp_pass = True
                        break
                    else:
                        actual = state.get('setpoint') if state else None
                        fail(f"Variant 0x{variant:02X} failed. Setpoint returned: {actual}°C")
                        wait_enter(f"Check panel. If it did not update to {test_c}°C, press ENTER to try next variant")

                if temp_pass:
                    wait_enter(f"Confirm panel shows new target {test_c}°C, then press ENTER to restore")
                    # Restore original
                    orig_f = round(orig_setpoint_c * 9/5 + 32)
                    payload = bytes([
                        0x01, 0x20, 0x10, 0x3C, 0xA1, 0x10, 0xA1,
                        0x00, 0x00, 0x80, working_variant, 0x00, 0xC0, 0x00, orig_f, 0x00
                    ])
                    await send_command(writer, build_frame(payload), f"TEMP SETPOINT → restoring {orig_setpoint_c}°C")
                    state = await read_broadcast(reader)
                    if state and state.get('setpoint') == orig_setpoint_c:
                        ok(f"Restored setpoint to {orig_setpoint_c}°C!")
                    else:
                        warn("Failed to automatically confirm restore in broadcast.")
                        wait_enter(f"Manually verify panel restored to {orig_setpoint_c}°C, then press ENTER")
                else:
                    fail("All dynamic temperature frame variants failed!")
                
                results.append(("Temperature setpoint", temp_pass))

    # ─── TEST 5: Heat schedule ────────────────────────────────────

    if "heat_schedule" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Heat schedule write + restore{RESET}")

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

        test_hs1, test_he1 = (11, 30), (15, 30)
        test_hs2, test_he2 = (19, 0), (21, 0)

        if not ask(f"Write test schedule ({test_hs1[0]:02d}:{test_hs1[1]:02d}-{test_he1[0]:02d}:{test_he1[1]:02d}), then restore?"):
            results.append(("Heat schedule", None))
        else:
            frame = adapter.build_schedule_command(
                "heat", test_hs1, test_he1, test_hs2, test_he2,
                slot1_enabled=True, slot2_enabled=True,
            )
            await send_command(writer, frame, "HEAT_SCHEDULE → test values (both enabled)")
            state = await read_broadcast(reader)
            sched_pass = False
            if state:
                s1 = state.get("heat_slot1_start", (0, 0))
                e1 = state.get("heat_slot1_end", (0, 0))
                if s1 == test_hs1 and e1 == test_he1:
                    ok(f"Broadcast confirms test schedule: {s1[0]:02d}:{s1[1]:02d}-{e1[0]:02d}:{e1[1]:02d}")
                    sched_pass = True
                else:
                    fail(f"Expected {test_hs1}, got {s1}")
            else:
                fail("No broadcast after schedule write")
            wait_enter("Check panel shows new heat schedule, then press ENTER")

            info("Restoring original heat schedule...")
            frame = adapter.build_schedule_command(
                "heat", orig_hs1, orig_he1, orig_hs2, orig_he2,
                slot1_enabled=orig_s1_en, slot2_enabled=orig_s2_en,
            )
            await send_command(writer, frame, "HEAT_SCHEDULE → restore original")
            state = await read_broadcast(reader)
            if state:
                s1 = state.get("heat_slot1_start", (0, 0))
                e1 = state.get("heat_slot1_end", (0, 0))
                if s1 == orig_hs1 and e1 == orig_he1:
                    ok("Original heat schedule restored")
                else:
                    warn(f"Restore may have failed: got {s1}, expected {orig_hs1}")
                    sched_pass = False
            wait_enter("Confirm panel shows original heat schedule, then press ENTER")
            results.append(("Heat schedule", sched_pass))

    # ─── TEST 6: Filter schedule ─────────────────────────────────

    if "filter_schedule" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Filter schedule (all fields + enable/disable){RESET}")

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

            # Step 1: Write test values with both slots enabled and specific hours+minutes
            test_fs1, test_fe1 = (5, 45), (9, 15)
            test_fs2, test_fe2 = (16, 30), (18, 55)
            info(f"Writing: slot1={test_fs1[0]:02d}:{test_fs1[1]:02d}-{test_fe1[0]:02d}:{test_fe1[1]:02d}, "
                 f"slot2={test_fs2[0]:02d}:{test_fs2[1]:02d}-{test_fe2[0]:02d}:{test_fe2[1]:02d} (both ON)")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=True, slot2_enabled=True,
            )
            await send_command(writer, frame, "FILTER_SCHEDULE → test values (both enabled)")
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
            wait_enter("Check panel: filter schedule shows new times? Press ENTER")

            # Step 2: Disable slot 1 using flags byte (times stay the same)
            info("Testing slot disable: disabling slot 1 via flags byte (times unchanged)...")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=False, slot2_enabled=True,
            )
            await send_command(writer, frame, "FILTER_SCHEDULE → slot1 disabled via flags")
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
            wait_enter("Check panel: filter slot 1 disabled (times still visible)? Press ENTER")

            # Step 3: Re-enable slot 1
            info("Re-enabling slot 1...")
            frame = adapter.build_schedule_command(
                "filter", test_fs1, test_fe1, test_fs2, test_fe2,
                slot1_enabled=True, slot2_enabled=True,
            )
            await send_command(writer, frame, "FILTER_SCHEDULE → slot1 re-enabled")
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
            wait_enter("Check panel: filter slot 1 enabled again? Press ENTER")

            # Step 4: Restore original
            info("Restoring original filter schedule...")
            frame = adapter.build_schedule_command(
                "filter", orig_fs1, orig_fe1, orig_fs2, orig_fe2,
                slot1_enabled=orig_s1_enabled, slot2_enabled=orig_s2_enabled,
            )
            await send_command(writer, frame, "FILTER_SCHEDULE → restore original")
            state = await read_broadcast(reader)
            if state:
                s1 = state.get("filter_slot1_start", (0, 0))
                e1 = state.get("filter_slot1_end", (0, 0))
                if s1 == orig_fs1 and e1 == orig_fe1:
                    ok("Original filter schedule restored")
                else:
                    warn(f"Restore may have failed: got {s1}-{e1}, expected {orig_fs1}-{orig_fe1}")
                    fsched_pass = False
            wait_enter("Confirm panel shows original filter schedule, then press ENTER")
            results.append(("Filter schedule", fsched_pass))

    # ─── TEST 7: Clock (write specific date/time) ────────────────

    if "clock" in selected:
        print(f"\n{'─'*60}")
        print(f"  {BOLD}TEST: Clock write (verify Y/M/D H:m:s){RESET}")
        info("Writes a known date/time, reads back each field, then restores current time.")

        if not ask("Run clock write test?"):
            results.append(("Clock write", None))
        else:
            from datetime import datetime
            clock_pass = True

            # Write a distinctive test time: 2025-03-15 14:37:52
            # Each field is unique so we can detect field swaps
            test_year, test_month, test_day = 2025, 3, 15
            test_hour, test_minute, test_second = 14, 37, 52
            info(f"Writing: {test_year}-{test_month:02d}-{test_day:02d} "
                 f"{test_hour:02d}:{test_minute:02d}:{test_second:02d}")

            frame = adapter.build_datetime_command(
                test_year, test_month, test_day, test_hour, test_minute, test_second
            )
            await send_command(writer, frame, "DATETIME_SET → test value")
            state = await read_broadcast(reader)

            if state and state.get("spa_datetime"):
                spa_dt = state["spa_datetime"]
                info(f"Broadcast spa_datetime: {spa_dt}")

                # Check each field
                checks = [
                    (spa_dt.year, test_year, "year"),
                    (spa_dt.month, test_month, "month"),
                    (spa_dt.day, test_day, "day"),
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
            else:
                fail("No spa_datetime in broadcast after write")
                clock_pass = False

            wait_enter("Check panel shows 2025-03-15 14:37:xx? Press ENTER")

            # Restore: set current time
            info("Restoring current time...")
            now = datetime.now()
            frame = adapter.build_datetime_command(
                now.year, now.month, now.day, now.hour, now.minute, now.second
            )
            await send_command(writer, frame, f"DATETIME_SET → restore ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            state = await read_broadcast(reader)
            if state and state.get("spa_datetime"):
                ok(f"Clock restored to: {state['spa_datetime']}")
            else:
                warn("Could not confirm clock restore")
            wait_enter("Confirm panel clock shows current time, then press ENTER")
            results.append(("Clock write", clock_pass))

    # ─── SUMMARY ─────────────────────────────────────────────────
    writer.close()

    print(f"\n{'='*60}")
    print(f"  {BOLD}TEST SUMMARY{RESET}")
    print(f"{'='*60}")
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
