#!/usr/bin/env python3
"""Capture panel commands when changing schedule slot times while DISABLED.

Guided capture: records what the PB554 panel sends for each of 4 scenarios:
  1. Heat slot 1 — change a time while slot 1 is DISABLED
  2. Heat slot 2 — change a time while slot 2 is DISABLED
  3. Filter slot 1 — change a time while slot 1 is DISABLED
  4. Filter slot 2 — change a time while slot 2 is DISABLED

This is sufficient to compare: does the panel use the same flags byte for
slot 1 disabled as for slot 2 disabled? If yes, the "slot 2 quirk" doesn't
exist and both slots behave identically.

Each capture: press Enter to start, timer counts, press Enter to stop.
All raw data saved as .bin files + JSONL session log.

Usage:
    source .venv/bin/activate
    python tools/capture_schedule_changes.py

Requires .env with SPA_BRIDGE_HOST (and optionally SPA_BRIDGE_PORT).
"""
from __future__ import annotations

import asyncio
import json
import os
import select
import sys
import threading
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

adapter = P25B85Adapter()

# ─── Output ──────────────────────────────────────────────────────
CAPTURE_DIR = Path(__file__).resolve().parent / "captures_schedule_changes"
CAPTURE_DIR.mkdir(exist_ok=True)

session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = CAPTURE_DIR / f"session_{session_ts}.jsonl"
_log_file = None

# ANSI colors
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
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


# ─── TCP capture with live timer ─────────────────────────────────

def capture_until_enter(host: str, port: int) -> tuple[bytes, float]:
    """Capture raw TCP data until user presses Enter. Shows live timer."""
    import socket
    sock = socket.create_connection((host, port), timeout=10.0)
    sock.settimeout(0.5)
    buf = bytearray()
    stop_event = threading.Event()
    start_time = time.time()

    def _reader():
        while not stop_event.is_set():
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            except socket.timeout:
                continue
            except OSError:
                break

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while True:
            elapsed = time.time() - start_time
            frames_so_far = len(find_frames(bytes(buf)))
            print(
                f"\r     ⏱  Recording... {elapsed:.0f}s  |  "
                f"{len(buf)} bytes  |  {frames_so_far} frames  "
                f"(press Enter to stop)",
                end="", flush=True,
            )
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                sys.stdin.readline()
                break
    except (KeyboardInterrupt, EOFError):
        pass

    stop_event.set()
    reader_thread.join(timeout=2.0)
    sock.close()

    duration = time.time() - start_time
    print(f"\r     ⏱  Recording stopped after {duration:.1f}s" + " " * 40)
    return bytes(buf), duration


def analyze_capture(data: bytes, label: str) -> list[bytes]:
    """Analyze captured data and print summary. Returns command frames."""
    frames = find_frames(data)
    broadcasts = [f for f in frames if is_broadcast(f)]
    commands = [f for f in frames if not is_broadcast(f)]

    info(f"Captured: {len(data)} bytes, {len(frames)} frames "
         f"({len(broadcasts)} broadcast, {len(commands)} command)")

    # Log all broadcast hex for completeness
    if broadcasts:
        _log_event("broadcasts_captured", label=label,
                   count=len(broadcasts),
                   frames=[f.hex() for f in broadcasts])

    if commands:
        print(f"  {BOLD}  Command frames found:{RESET}")
        for idx, cmd_frame in enumerate(commands):
            inner = cmd_frame[1:-1]
            unesc = pseudo_unescape(inner)
            print(f"     [{idx}] wire: {cmd_frame.hex()}")
            if len(unesc) >= 16:
                cmd_type = unesc[4] if len(unesc) > 4 else 0
                flags_byte = unesc[7] if len(unesc) > 7 else 0
                type_str = {0xA1: "button", 0xA2: "datetime",
                            0xA3: "heat_sched", 0xA4: "filter_sched"}.get(cmd_type, f"0x{cmd_type:02X}")
                print(f"          type={type_str}, flags=0x{flags_byte:02X}")
                print(f"          payload: {unesc[:16].hex()}")
                if cmd_type in (0xA3, 0xA4):
                    s1_sh, s1_sm = unesc[8], unesc[9]
                    s1_eh, s1_em = unesc[10], unesc[11]
                    s2_sh, s2_sm = unesc[12], unesc[13]
                    s2_eh, s2_em = unesc[14], unesc[15]
                    print(f"          slot1: {s1_sh:02d}:{s1_sm:02d}-{s1_eh:02d}:{s1_em:02d}")
                    print(f"          slot2: {s2_sh:02d}:{s2_sm:02d}-{s2_eh:02d}:{s2_em:02d}")
        _log_event("commands_found", label=label,
                   commands=[f.hex() for f in commands],
                   payloads=[pseudo_unescape(f[1:-1])[:16].hex() for f in commands
                             if len(pseudo_unescape(f[1:-1])) >= 16])
    else:
        warn("No command frames found in capture")
        _log_event("commands_found", label=label, commands=[])

    # Show last broadcast state
    for raw_frame in reversed(broadcasts):
        try:
            logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
            result = adapter.parse_status(logical)
            if result:
                info(f"Heat:   {format_schedule(result, 'heat')}")
                info(f"Filter: {format_schedule(result, 'filter')}")
                safe = {}
                for k, v in result.items():
                    if isinstance(v, (str, int, float, bool)) or v is None:
                        safe[k] = v
                    elif hasattr(v, "isoformat"):
                        safe[k] = v.isoformat()
                    elif isinstance(v, tuple):
                        safe[k] = list(v)
                    else:
                        safe[k] = str(v)
                _log_event("broadcast_state_after", label=label,
                           raw_hex=raw_frame.hex(),
                           logical_hex=logical.hex(),
                           parsed=safe)
                break
        except Exception:
            continue

    return commands


async def read_broadcast_async(reader: asyncio.StreamReader) -> tuple[dict | None, bytes]:
    """Read a broadcast frame using asyncio. Returns (parsed_state, raw_bytes)."""
    deadline = time.monotonic() + BROADCAST_TIMEOUT
    buf = bytearray()
    all_raw = bytearray()
    latest: dict | None = None

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
        all_raw.extend(chunk)
        for raw_frame in find_frames(bytes(buf)):
            if not validate_frame(raw_frame) or not is_broadcast(raw_frame):
                continue
            try:
                logical = unescape_frame(raw_frame, full=adapter.unescape_full_frame)
                result = adapter.parse_status(logical)
                if result is not None:
                    latest = result
            except Exception:
                continue
        last_end = bytes(buf).rfind(b"\x1d")
        if last_end >= 0:
            buf = buf[last_end + 1:]
        if latest is not None and not buf:
            break
    return latest, bytes(all_raw)


# ─── Guided capture steps ─────────────────────────────────────────

CAPTURE_STEPS = [
    {
        "id": "heat_slot1_disabled",
        "schedule": "heat",
        "slot": 1,
        "title": "Heat slot 1 — change a time while DISABLED",
        "instruction": (
            "On the PB554 panel, go to heat schedule settings.\n"
            "  Make sure slot 1 is DISABLED.\n"
            "  Change slot 1 START or END time to a different value.\n"
            "  (The panel should let you edit the time even while disabled.)"
        ),
    },
    {
        "id": "heat_slot2_disabled",
        "schedule": "heat",
        "slot": 2,
        "title": "Heat slot 2 — change a time while DISABLED",
        "instruction": (
            "On the PB554 panel, go to heat schedule settings.\n"
            "  Make sure slot 2 is DISABLED.\n"
            "  Change slot 2 START or END time to a different value."
        ),
    },
    {
        "id": "filter_slot1_disabled",
        "schedule": "filter",
        "slot": 1,
        "title": "Filter slot 1 — change a time while DISABLED",
        "instruction": (
            "On the PB554 panel, go to filter schedule settings.\n"
            "  Make sure slot 1 is DISABLED.\n"
            "  Change slot 1 START or END time to a different value."
        ),
    },
    {
        "id": "filter_slot2_disabled",
        "schedule": "filter",
        "slot": 2,
        "title": "Filter slot 2 — change a time while DISABLED",
        "instruction": (
            "On the PB554 panel, go to filter schedule settings.\n"
            "  Make sure slot 2 is DISABLED.\n"
            "  Change slot 2 START or END time to a different value."
        ),
    },
]


async def run() -> None:
    global _log_file

    print(f"\n{'='*70}")
    print(f"  {BOLD}Capture: Schedule Slot Changes While Disabled{RESET}")
    print(f"{'='*70}")
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Log:  {LOG_PATH}")
    print(f"  Bin:  {CAPTURE_DIR}/")
    print(f"{'='*70}")
    print()
    print(f"  {BOLD}What this captures:{RESET}")
    print(f"  For each schedule type (heat + filter) and each slot (1 + 2),")
    print(f"  record what command the PB554 panel sends when you change a")
    print(f"  time while that slot is DISABLED.")
    print()
    print(f"  {BOLD}Why:{RESET}")
    print(f"  We want to compare the flags byte the panel uses for slot 1")
    print(f"  vs slot 2. If they differ, slot 2 has a quirk. If they're")
    print(f"  the same, both slots work identically.")
    print()
    print(f"  {BOLD}Steps: 4 captures{RESET}")
    for i, step in enumerate(CAPTURE_STEPS, 1):
        print(f"    {i}. {step['title']}")
    print()
    print(f"  Each capture: press Enter to start → make change → press Enter to stop")
    print()

    if not HOST:
        print(f"{RED}ERROR: SPA_BRIDGE_HOST not set in .env{RESET}")
        return

    try:
        input(f"  {BOLD}>>> Press ENTER to connect and start...{RESET}")
    except (KeyboardInterrupt, EOFError):
        return

    _log_file = open(LOG_PATH, "a")
    _log_event("session_start", host=HOST, port=PORT)

    # ─── Read baseline state ──────────────────────────────────────
    info("Connecting to read initial state...")
    try:
        reader, writer = await asyncio.open_connection(HOST, PORT)
    except Exception as e:
        fail(f"Cannot connect: {e}")
        return
    ok("Connected")

    state, baseline_raw = await read_broadcast_async(reader)
    writer.close()

    # Save baseline raw
    if baseline_raw:
        baseline_path = CAPTURE_DIR / f"baseline_{session_ts}.bin"
        with open(baseline_path, "wb") as f:
            f.write(baseline_raw)
        ok(f"Baseline saved: {baseline_path.name} ({len(baseline_raw)} bytes)")

    if state:
        print()
        info(f"Heat:   {format_schedule(state, 'heat')}")
        info(f"Filter: {format_schedule(state, 'filter')}")
        _log_event("baseline",
                   heat=format_schedule(state, 'heat'),
                   filter=format_schedule(state, 'filter'))

        # Check which slots are enabled — warn user to disable them
        for prefix, label in [("heat", "Heat"), ("filter", "Filter")]:
            for slot in (1, 2):
                enabled = state.get(f"{prefix}_slot{slot}_enabled", False)
                if enabled:
                    warn(f"{label} slot {slot} is currently ENABLED — "
                         f"you'll need to disable it before that capture step.")
    else:
        warn("Could not read initial state — continuing anyway")

    print()

    # ─── Run guided captures ──────────────────────────────────────
    captured_results: list[dict] = []

    try:
        for step_idx, step in enumerate(CAPTURE_STEPS):
            step_num = step_idx + 1
            step_id = step["id"]

            print(f"\n{'─'*70}")
            print(f"  {BOLD}[{step_num}/4] {step['title']}{RESET}")
            print(f"{'─'*70}")
            print()

            # Show current state for this schedule/slot
            if state:
                prefix = step["schedule"]
                slot = step["slot"]
                slot_start = state.get(f"{prefix}_slot{slot}_start", (0, 0))
                slot_end = state.get(f"{prefix}_slot{slot}_end", (0, 0))
                enabled = state.get(f"{prefix}_slot{slot}_enabled", False)
                info(f"Current {prefix} slot {slot}: "
                     f"{slot_start[0]:02d}:{slot_start[1]:02d}-{slot_end[0]:02d}:{slot_end[1]:02d} "
                     f"({'ENABLED' if enabled else 'DISABLED'})")
                if enabled:
                    warn(f"Slot is ENABLED! Please disable it on the panel first.")
            print()
            print(f"  {BOLD}📋 Instructions:{RESET}")
            print(f"  {step['instruction']}")
            print()

            # Ask to proceed or skip
            try:
                resp = input(f"  {BOLD}>>> Ready? [Enter=start / s=skip / q=quit]: {RESET}").strip().lower()
            except (KeyboardInterrupt, EOFError):
                break
            if resp in ("q", "quit", "exit"):
                break
            if resp in ("s", "skip"):
                info(f"Skipping {step_id}")
                _log_event("step_skipped", step=step_id)
                captured_results.append({"step": step_id, "skipped": True})
                continue

            # Pre-capture state
            info("Reading pre-capture state...")
            try:
                pre_reader, pre_writer = await asyncio.open_connection(HOST, PORT)
                pre_state, _ = await read_broadcast_async(pre_reader)
                pre_writer.close()
                if pre_state:
                    _log_event("pre_capture_state", step=step_id,
                               heat=format_schedule(pre_state, 'heat'),
                               filter=format_schedule(pre_state, 'filter'))
            except Exception:
                pre_state = state

            # Start capture
            print()
            print(f"  {BOLD}Recording starts NOW.{RESET}")
            print(f"  Make the change on the panel, then press ENTER when done.")
            print()

            filename = f"{step_num:02d}_{step_id}.bin"
            filepath = CAPTURE_DIR / filename

            try:
                data, duration = capture_until_enter(HOST, PORT)
            except Exception as e:
                fail(f"Capture error: {e}")
                _log_event("capture_error", step=step_id, error=str(e))
                captured_results.append({"step": step_id, "error": str(e)})
                continue

            # Save binary
            with open(filepath, "wb") as f:
                f.write(data)
            ok(f"Saved: {filename} ({len(data)} bytes, {duration:.1f}s)")
            _log_event("capture_saved", step=step_id, filename=filename,
                       bytes=len(data), duration_s=round(duration, 2))

            # Analyze
            print()
            commands = analyze_capture(data, step_id)

            # Post-capture state
            info("Reading post-capture state...")
            try:
                post_reader, post_writer = await asyncio.open_connection(HOST, PORT)
                post_state, post_raw = await read_broadcast_async(post_reader)
                post_writer.close()

                if post_state:
                    # Save post .bin
                    post_path = CAPTURE_DIR / f"{step_num:02d}_{step_id}_post.bin"
                    with open(post_path, "wb") as f:
                        f.write(post_raw)

                    _log_event("post_capture_state", step=step_id,
                               heat=format_schedule(post_state, 'heat'),
                               filter=format_schedule(post_state, 'filter'))

                    # Show what changed
                    if pre_state:
                        changes = []
                        for pfx in ("heat", "filter"):
                            for key in ("slot1_start", "slot1_end", "slot2_start", "slot2_end",
                                        "slot1_enabled", "slot2_enabled"):
                                k = f"{pfx}_{key}"
                                old_val = pre_state.get(k)
                                new_val = post_state.get(k)
                                if old_val != new_val:
                                    changes.append((k, old_val, new_val))
                                    ok(f"CHANGED {k}: {old_val} → {new_val}")
                        if not changes:
                            warn("No schedule changes detected in broadcast!")
                        _log_event("changes_detected", step=step_id,
                                   changes=[(k, str(o), str(n)) for k, o, n in changes])
                    state = post_state
            except Exception as e:
                warn(f"Could not read post-capture state: {e}")

            # Summarize this step
            result_info = {
                "step": step_id,
                "filename": filename,
                "commands": len(commands),
                "flags_bytes": [],
            }
            for cmd_frame in commands:
                unesc = pseudo_unescape(cmd_frame[1:-1])
                if len(unesc) >= 16:
                    result_info["flags_bytes"].append(f"0x{unesc[7]:02X}")
            captured_results.append(result_info)

            # Notes
            try:
                notes = input(f"\n  {DIM}Notes (optional, Enter to skip): {RESET}").strip()
            except (KeyboardInterrupt, EOFError):
                break
            if notes:
                _log_event("notes", step=step_id, notes=notes)

    except KeyboardInterrupt:
        print(f"\n\n  {YELLOW}⏹️  Aborted.{RESET}")

    # ─── Summary ──────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  {BOLD}CAPTURE SUMMARY{RESET}")
    print(f"{'='*70}")
    print()

    # Compare flags bytes across captures
    flags_comparison: dict[str, list[str]] = {}
    for res in captured_results:
        if res.get("skipped") or res.get("error"):
            step = res["step"]
            print(f"  {YELLOW}⏭️{RESET}  {step} — {'skipped' if res.get('skipped') else 'error'}")
            continue
        step = res["step"]
        flags = res.get("flags_bytes", [])
        print(f"  {GREEN}✅{RESET} {step} — {res.get('commands', 0)} command(s), "
              f"flags: {', '.join(flags) if flags else 'none'}")
        flags_comparison[step] = flags

    print()
    if flags_comparison:
        # Check if slot 1 and slot 2 use the same flags
        heat_s1_flags = flags_comparison.get("heat_slot1_disabled", [])
        heat_s2_flags = flags_comparison.get("heat_slot2_disabled", [])
        filter_s1_flags = flags_comparison.get("filter_slot1_disabled", [])
        filter_s2_flags = flags_comparison.get("filter_slot2_disabled", [])

        print(f"  {BOLD}Flags byte comparison:{RESET}")
        print(f"    Heat slot 1 (disabled):   {', '.join(heat_s1_flags) if heat_s1_flags else '(no data)'}")
        print(f"    Heat slot 2 (disabled):   {', '.join(heat_s2_flags) if heat_s2_flags else '(no data)'}")
        print(f"    Filter slot 1 (disabled): {', '.join(filter_s1_flags) if filter_s1_flags else '(no data)'}")
        print(f"    Filter slot 2 (disabled): {', '.join(filter_s2_flags) if filter_s2_flags else '(no data)'}")
        print()

        all_flags = heat_s1_flags + heat_s2_flags + filter_s1_flags + filter_s2_flags
        if all_flags:
            unique_flags = set(all_flags)
            if len(unique_flags) == 1:
                ok(f"All captures use the SAME flags byte: {unique_flags.pop()}")
                info("→ Slot 1 and slot 2 behave identically when disabled.")
                info("→ The force-write mechanism applies to BOTH slots equally.")
            else:
                warn(f"DIFFERENT flags bytes detected: {unique_flags}")
                s1_flags = set(heat_s1_flags + filter_s1_flags)
                s2_flags = set(heat_s2_flags + filter_s2_flags)
                if s1_flags != s2_flags:
                    info(f"→ Slot 1 uses: {s1_flags}")
                    info(f"→ Slot 2 uses: {s2_flags}")
                    info("→ The slot 2 quirk IS confirmed — slots behave differently.")
    print()
    info(f"Captures saved to: {CAPTURE_DIR}/")
    info(f"Session log: {LOG_PATH}")
    print()

    _log_event("session_end",
               results=[{k: v for k, v in r.items()} for r in captured_results])
    if _log_file:
        _log_file.close()


if __name__ == "__main__":
    asyncio.run(run())

