#!/usr/bin/env python3
"""Capture panel commands when changing BOTH slot 1 & 2 times at once while disabled.

Guided capture: records what the PB554 panel sends for 2 scenarios:
  1. Heat — change both slot 1 and slot 2 times in one save (both DISABLED)
  2. Filter — change both slot 1 and slot 2 times in one save (both DISABLED)

This complements capture_schedule_changes.py (which captures individual slots).
Here we want to see: when the user edits both slots in one go, does the panel
send one command or two? What flags byte is used?

Each capture: press Enter to start, timer counts, press Enter to stop.
All raw data saved as .bin files + JSONL session log.

Usage:
    source .venv/bin/activate
    python tools/capture_schedule_both_slots.py

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
BROADCAST_TIMEOUT = 5.0

adapter = P25B85Adapter()

# ─── Output ──────────────────────────────────────────────────────
CAPTURE_DIR = Path(__file__).resolve().parent / "captures_schedule_both"
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
        "id": "heat_both_slots_disabled",
        "schedule": "heat",
        "title": "Heat — change BOTH slot 1 AND slot 2 times (both DISABLED)",
        "instruction": (
            "On the PB554 panel, go to heat schedule settings.\n"
            "  Make sure BOTH slot 1 and slot 2 are DISABLED.\n"
            "  Change a time in slot 1 AND a time in slot 2, then save.\n"
            "  (Edit both before pressing confirm/save on the panel.)"
        ),
    },
    {
        "id": "filter_both_slots_disabled",
        "schedule": "filter",
        "title": "Filter — change BOTH slot 1 AND slot 2 times (both DISABLED)",
        "instruction": (
            "On the PB554 panel, go to filter schedule settings.\n"
            "  Make sure BOTH slot 1 and slot 2 are DISABLED.\n"
            "  Change a time in slot 1 AND a time in slot 2, then save.\n"
            "  (Edit both before pressing confirm/save on the panel.)"
        ),
    },
]


async def run() -> None:
    global _log_file

    print(f"\n{'='*70}")
    print(f"  {BOLD}Capture: Both Slots Changed At Once (While Disabled){RESET}")
    print(f"{'='*70}")
    print(f"  Host: {HOST}:{PORT}")
    print(f"  Log:  {LOG_PATH}")
    print(f"  Bin:  {CAPTURE_DIR}/")
    print(f"{'='*70}")
    print()
    print(f"  {BOLD}What this captures:{RESET}")
    print(f"  Record what the PB554 panel sends when you change times in")
    print(f"  BOTH slot 1 and slot 2 in a single save (both disabled).")
    print()
    print(f"  {BOLD}Why:{RESET}")
    print(f"  Does the panel send one command with both slots, or two")
    print(f"  separate commands? What flags byte is used?")
    print()
    print(f"  {BOLD}Steps: 2 captures{RESET}")
    for i, step in enumerate(CAPTURE_STEPS, 1):
        print(f"    {i}. {step['title']}")
    print()
    print(f"  Each capture: press Enter to start → make changes → press Enter to stop")
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
                         f"disable it before that capture step.")
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
            print(f"  {BOLD}[{step_num}/2] {step['title']}{RESET}")
            print(f"{'─'*70}")
            print()

            # Show current state
            if state:
                prefix = step["schedule"]
                info(f"Current {prefix}: {format_schedule(state, prefix)}")
                s1_en = state.get(f"{prefix}_slot1_enabled", False)
                s2_en = state.get(f"{prefix}_slot2_enabled", False)
                if s1_en or s2_en:
                    warn(f"One or both slots are ENABLED! Disable them first.")
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
            print(f"  Change BOTH slot 1 and slot 2 times, then press ENTER when done.")
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

    for res in captured_results:
        if res.get("skipped") or res.get("error"):
            step = res["step"]
            print(f"  {YELLOW}⏭️{RESET}  {step} — {'skipped' if res.get('skipped') else 'error'}")
            continue
        step = res["step"]
        flags = res.get("flags_bytes", [])
        num_cmds = res.get("commands", 0)
        print(f"  {GREEN}✅{RESET} {step} — {num_cmds} command(s), "
              f"flags: {', '.join(flags) if flags else 'none'}")

        if num_cmds == 1:
            info(f"  → Panel sends ONE command for both slots")
        elif num_cmds == 2:
            info(f"  → Panel sends TWO separate commands (one per slot)")
        elif num_cmds > 2:
            warn(f"  → Unexpected: {num_cmds} commands!")

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

