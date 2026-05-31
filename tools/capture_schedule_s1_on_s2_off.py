#!/usr/bin/env python3
"""Capture PB554 schedule commands for the s1=ON, s2=OFF slot2-edit scenario.

Goal:
- Observe what flags byte the panel sends when slot 1 is enabled,
  slot 2 is disabled, and slot 2 time is edited/saved.

Captures two guided steps:
1) Heat schedule: slot1 ON, slot2 OFF, edit slot2 time
2) Filter schedule: slot1 ON, slot2 OFF, edit slot2 time

Usage:
    source .venv/bin/activate
    python tools/capture_schedule_s1_on_s2_off.py
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
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import types

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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

CAPTURE_DIR = Path(__file__).resolve().parent / "captures_schedule_s1_on_s2_off"
CAPTURE_DIR.mkdir(exist_ok=True)
session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
LOG_PATH = CAPTURE_DIR / f"session_{session_ts}.jsonl"
_log_file = None


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


def format_schedule(data: dict, prefix: str) -> str:
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


def capture_until_enter(host: str, port: int) -> tuple[bytes, float]:
    """Capture raw TCP data until user presses Enter."""
    import socket

    sock = socket.create_connection((host, port), timeout=10.0)
    sock.settimeout(0.5)
    buf = bytearray()
    stop_event = threading.Event()
    start_time = time.time()

    def _reader() -> None:
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
            frames = len(find_frames(bytes(buf)))
            print(
                f"\r  recording {elapsed:.0f}s | {len(buf)} bytes | {frames} frames (press Enter to stop)",
                end="",
                flush=True,
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
    print(f"\r  recording stopped after {duration:.1f}s" + " " * 20)
    return bytes(buf), duration


def extract_schedule_commands(data: bytes) -> list[dict]:
    """Extract schedule commands (A3/A4) with flags + payload details."""
    result: list[dict] = []
    for raw in find_frames(data):
        if not validate_frame(raw) or is_broadcast(raw):
            continue
        unesc = pseudo_unescape(raw[1:-1])
        if len(unesc) < 16:
            continue
        cmd_type = unesc[4]
        if cmd_type not in (0xA3, 0xA4):
            continue
        result.append(
            {
                "wire": raw.hex(),
                "payload": unesc[:16].hex(),
                "cmd_type": "heat" if cmd_type == 0xA3 else "filter",
                "flags": f"0x{unesc[7]:02X}",
                "slot1": f"{unesc[8]:02d}:{unesc[9]:02d}-{unesc[10]:02d}:{unesc[11]:02d}",
                "slot2": f"{unesc[12]:02d}:{unesc[13]:02d}-{unesc[14]:02d}:{unesc[15]:02d}",
            }
        )
    return result


async def read_broadcast_async(reader: asyncio.StreamReader) -> tuple[dict | None, bytes]:
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
                parsed = adapter.parse_status(logical)
                if parsed is not None:
                    latest = parsed
            except Exception:
                continue

        last_end = bytes(buf).rfind(b"\x1d")
        if last_end >= 0:
            buf = buf[last_end + 1 :]
        if latest is not None and not buf:
            break

    return latest, bytes(all_raw)


CAPTURE_STEPS = [
    {
        "id": "heat_s1_on_s2_off_slot2_edit",
        "schedule": "heat",
        "title": "Heat: slot1 ON, slot2 OFF, edit slot2 time",
        "instruction": (
            "On PB554 heat schedule:\n"
            "  1) Enable slot 1\n"
            "  2) Disable slot 2\n"
            "  3) Change slot 2 start or end time\n"
            "  4) Save/apply"
        ),
    },
    {
        "id": "filter_s1_on_s2_off_slot2_edit",
        "schedule": "filter",
        "title": "Filter: slot1 ON, slot2 OFF, edit slot2 time",
        "instruction": (
            "On PB554 filter schedule:\n"
            "  1) Enable slot 1\n"
            "  2) Disable slot 2\n"
            "  3) Change slot 2 start or end time\n"
            "  4) Save/apply"
        ),
    },
]


async def run() -> None:
    global _log_file

    print("\n" + "=" * 70)
    print("Capture: panel flags for s1=ON, s2=OFF, slot2 edit")
    print("=" * 70)
    print(f"Host: {HOST}:{PORT}")
    print(f"Log:  {LOG_PATH}")
    print(f"Bin:  {CAPTURE_DIR}/")
    print("=" * 70)

    if not HOST:
        print("ERROR: SPA_BRIDGE_HOST not set in .env")
        return

    input("\nPress ENTER to connect and start...")

    _log_file = open(LOG_PATH, "a")
    _log_event("session_start", host=HOST, port=PORT)

    # Baseline
    reader, writer = await asyncio.open_connection(HOST, PORT)
    baseline, baseline_raw = await read_broadcast_async(reader)
    writer.close()

    if baseline_raw:
        baseline_path = CAPTURE_DIR / f"baseline_{session_ts}.bin"
        baseline_path.write_bytes(baseline_raw)

    if baseline:
        print(f"\nBaseline heat:   {format_schedule(baseline, 'heat')}")
        print(f"Baseline filter: {format_schedule(baseline, 'filter')}")
        _log_event(
            "baseline",
            heat=format_schedule(baseline, "heat"),
            filter=format_schedule(baseline, "filter"),
        )

    results: list[dict] = []

    for idx, step in enumerate(CAPTURE_STEPS, start=1):
        print("\n" + "-" * 70)
        print(f"[{idx}/2] {step['title']}")
        print("-" * 70)
        print(step["instruction"])

        resp = input("\nReady? [Enter=start / s=skip / q=quit]: ").strip().lower()
        if resp in ("q", "quit", "exit"):
            break
        if resp in ("s", "skip"):
            results.append({"step": step["id"], "skipped": True})
            _log_event("step_skipped", step=step["id"])
            continue

        print("\nRecording starts now. Perform panel action, then press ENTER here.")
        data, duration = capture_until_enter(HOST, PORT)
        filename = f"{idx:02d}_{step['id']}.bin"
        (CAPTURE_DIR / filename).write_bytes(data)

        commands = extract_schedule_commands(data)
        _log_event(
            "capture",
            step=step["id"],
            file=filename,
            bytes=len(data),
            duration_s=round(duration, 2),
            commands=commands,
        )

        print(f"Captured {len(data)} bytes in {duration:.1f}s")
        if not commands:
            print("No schedule command frames found")
        else:
            print("Schedule commands:")
            for c in commands:
                print(
                    f"  type={c['cmd_type']} flags={c['flags']} slot1={c['slot1']} slot2={c['slot2']}"
                )

        # Post-state
        r2, w2 = await asyncio.open_connection(HOST, PORT)
        post, post_raw = await read_broadcast_async(r2)
        w2.close()
        if post_raw:
            (CAPTURE_DIR / f"{idx:02d}_{step['id']}_post.bin").write_bytes(post_raw)
        if post:
            print(f"Post heat:   {format_schedule(post, 'heat')}")
            print(f"Post filter: {format_schedule(post, 'filter')}")
            _log_event(
                "post_state",
                step=step["id"],
                heat=format_schedule(post, "heat"),
                filter=format_schedule(post, "filter"),
            )

        flags = [c["flags"] for c in commands]
        results.append({"step": step["id"], "flags": flags, "commands": len(commands)})

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for res in results:
        if res.get("skipped"):
            print(f"{res['step']}: skipped")
            continue
        flags = res.get("flags", [])
        print(f"{res['step']}: commands={res.get('commands', 0)} flags={', '.join(flags) if flags else 'none'}")

    heat_flags = []
    filter_flags = []
    for res in results:
        if res.get("step") == "heat_s1_on_s2_off_slot2_edit":
            heat_flags = res.get("flags", [])
        if res.get("step") == "filter_s1_on_s2_off_slot2_edit":
            filter_flags = res.get("flags", [])

    if heat_flags or filter_flags:
        print("\nDecision hint:")
        print(f"  Heat flags observed:   {', '.join(heat_flags) if heat_flags else 'none'}")
        print(f"  Filter flags observed: {', '.join(filter_flags) if filter_flags else 'none'}")
        if set(heat_flags + filter_flags) == {"0x68"}:
            print("  Panel appears to use 0x68 for this scenario.")

    print(f"\nSaved captures in: {CAPTURE_DIR}/")
    print(f"Session log: {LOG_PATH}")

    _log_event("session_end", results=results)
    if _log_file:
        _log_file.close()


if __name__ == "__main__":
    asyncio.run(run())

