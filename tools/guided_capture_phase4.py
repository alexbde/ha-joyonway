#!/usr/bin/env python3
"""
Phase 4 — Command frame capture tool for Joyonway P25B85.

Captures RS485 bus traffic while you press buttons on the PB554 panel.
The goal is to capture the COMMAND frames the panel sends to the controller,
which we can later replay from Home Assistant for write support.

Key difference from Phase 3 captures:
  - Phase 3 captured steady-state BROADCAST frames (controller → bus)
  - Phase 4 captures COMMAND frames (panel → controller) at button press moment

Instructions:
  - Press the button DURING the "active" capture window (not before!)
  - Wait 2-3 seconds into the capture, then press once
  - The command frame is very brief — the capture window is longer to ensure we get it

Python stdlib only — no pip dependencies required.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import signal
import socket
import sys
import time

__version__ = "2.0.0-phase4"

# Load .env if present (for personal bridge IP)
def _load_dotenv():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_dotenv()

_DEFAULT_HOST = os.environ.get("SPA_BRIDGE_HOST", "192.168.1.100")
_DEFAULT_PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D

# Phase 4 actions: each one captures a button press command
PHASE4_ACTIONS = [
    ("cmd_pump_on",
     "PUMP ON — press pump button ONCE during capture (from OFF → low)"),
    ("cmd_pump_high",
     "PUMP HIGH — press pump button ONCE during capture (from low → high)"),
    ("cmd_pump_off",
     "PUMP OFF — press pump button ONCE during capture (from high → OFF)"),
    ("cmd_light_on",
     "LIGHT ON — press light button ONCE during capture (from OFF → ON)"),
    ("cmd_light_off",
     "LIGHT OFF — press light button ONCE during capture (from ON → OFF)"),
    ("cmd_temp_up",
     "TEMP UP — press temperature UP button ONCE during capture"),
    ("cmd_temp_down",
     "TEMP DOWN — press temperature DOWN button ONCE during capture"),
]

# Two phases per action: baseline (no press) and press (button pressed during capture)
PHASES = ["baseline", "press"]
PHASE_INSTRUCTIONS = {
    "baseline": "Do NOT touch any buttons. Just let the bus idle for reference.",
    "press": (
        "⚡ Press the button ONCE during this capture!\n"
        "     Wait ~3 seconds after capture starts, then press.\n"
        "     Press only ONCE — we need a clean single command frame."
    ),
}


# ──────────────────────────────────────────────────────────────
# Frame counting & analysis helpers
# ──────────────────────────────────────────────────────────────

def count_frames(data: bytes) -> tuple[int, int, int]:
    """Count total frames, broadcast frames, and non-broadcast frames.

    Returns (frame_count, broadcast_count, command_candidate_count).
    """
    frames = 0
    broadcasts = 0
    others = 0
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frame = data[i : j + 1]
                    frames += 1
                    if len(frame) > 1 and frame[1] == 0xFF:
                        broadcasts += 1
                    else:
                        others += 1
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames, broadcasts, others


def extract_non_broadcast_frames(data: bytes) -> list[bytes]:
    """Extract all non-broadcast frames (potential command frames)."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frame = data[i : j + 1]
                    if len(frame) > 1 and frame[1] != 0xFF:
                        frames.append(frame)
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


# ──────────────────────────────────────────────────────────────
# TCP capture
# ──────────────────────────────────────────────────────────────

def capture_segment(host: str, port: int, duration: float, timeout: float = 10.0) -> bytes:
    """Connect to TCP bridge and capture raw bytes for `duration` seconds."""
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(1.0)
    buf = bytearray()
    deadline = time.time() + duration
    try:
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf.extend(chunk)
            except socket.timeout:
                continue
    finally:
        sock.close()
    return bytes(buf)


def dry_run_capture(duration: float) -> bytes:
    """Simulate a capture for dry-run mode."""
    ref = bytes.fromhex(
        "1aff013cd2b4ff08035e040604f540006801001221123b"
        "1400160004004300043b120014000000064d0000000000"
        "000000000000001005081b1b111200004e28331d"
    )
    filler = bytes.fromhex("1a100103000000001d")
    result = bytearray()
    cycles = max(1, int(duration / 0.6))
    for _ in range(cycles):
        result.extend(filler)
        result.extend(ref)
    return bytes(result)


# ──────────────────────────────────────────────────────────────
# Session management
# ──────────────────────────────────────────────────────────────

class CaptureSession:
    """Manages the Phase 4 capture session."""

    def __init__(
        self,
        host: str,
        port: int,
        out_dir: str,
        dry_run: bool,
        existing_segments: list[dict] | None = None,
        started_at: str | None = None,
        segment_counter: int = 0,
        resumed: bool = False,
    ):
        self.host = host
        self.port = port
        self.out_dir = out_dir
        self.dry_run = dry_run
        self.segments: list[dict] = list(existing_segments or [])
        self.started_at = started_at or datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.segment_counter = segment_counter
        self.resumed = resumed
        self._interrupted = False

        os.makedirs(out_dir, exist_ok=True)

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    def mark_interrupted(self):
        self._interrupted = True

    def capture_phase(
        self, action: str, phase: str, duration: float, notes: str = ""
    ) -> dict:
        """Capture one segment and save to disk."""
        filename = f"{self.segment_counter:02d}_{action}_{phase}.bin"
        filepath = os.path.join(self.out_dir, filename)
        self.segment_counter += 1

        start_time = datetime.datetime.now(datetime.timezone.utc)

        if self.dry_run:
            data = dry_run_capture(duration)
        else:
            data = capture_segment(self.host, self.port, duration)

        end_time = datetime.datetime.now(datetime.timezone.utc)
        actual_duration = (end_time - start_time).total_seconds()

        frame_count, broadcast_count, cmd_count = count_frames(data)

        # Save raw data
        with open(filepath, "wb") as f:
            f.write(data)

        # Quick analysis: show non-broadcast frames found
        non_bc = extract_non_broadcast_frames(data)

        segment_info = {
            "action": action,
            "phase": phase,
            "filename": filename,
            "started_at": start_time.isoformat(),
            "ended_at": end_time.isoformat(),
            "duration_s": round(actual_duration, 2),
            "byte_count": len(data),
            "frame_count": frame_count,
            "broadcast_count": broadcast_count,
            "command_candidate_count": cmd_count,
            "notes": notes,
        }
        self.segments.append(segment_info)

        # Print immediate analysis for command candidates
        if non_bc and phase == "press":
            print(f"\n     🔍 Found {len(non_bc)} non-broadcast frame(s) — potential commands:")
            for idx, fr in enumerate(non_bc[:10]):  # show max 10
                print(f"        [{idx}] {fr.hex()}")
            if len(non_bc) > 10:
                print(f"        ... and {len(non_bc) - 10} more")

        return segment_info

    def save_manifest(self):
        """Write session_manifest.json."""
        manifest = {
            "session": {
                "started_at": self.started_at,
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "host": self.host,
                "port": self.port,
                "tool_version": __version__,
                "phase": "phase4_commands",
                "dry_run": self.dry_run,
                "resumed": self.resumed,
                "completed": not self._interrupted,
            },
            "segments": self.segments,
        }
        path = os.path.join(self.out_dir, "session_manifest.json")
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2)
        return path


# ──────────────────────────────────────────────────────────────
# Interactive UI
# ──────────────────────────────────────────────────────────────

def print_banner():
    print()
    print("=" * 66)
    print("  Joyonway Spa — Phase 4: Command Frame Capture")
    print(f"  Version {__version__}")
    print("=" * 66)
    print()
    print("This tool captures COMMAND frames sent by the PB554 panel")
    print("when you press buttons. These frames will be replayed by")
    print("Home Assistant for write support (pump, light, temp control).")
    print()
    print("⚡ KEY DIFFERENCE from Phase 3:")
    print("   Press the button DURING the 'press' capture window!")
    print("   Wait ~3s into the capture, then press ONCE.")
    print()


def print_bridge_warning():
    print("⚠️  IMPORTANT: RS485 bridge single-client limitation")
    print("─" * 50)
    print("Most RS485-to-WiFi bridges (EW11, W610) only accept ONE")
    print("TCP client at a time. Before starting:")
    print("  • Close the Elfin/USR phone app")
    print("  • Stop Home Assistant's Joyonway integration")
    print("  • Close any other tool connected to the bridge")
    print()


def prompt_continue(message: str = "Press Enter to start capture...") -> bool:
    try:
        input(message)
        return True
    except (KeyboardInterrupt, EOFError):
        return False


def prompt_notes(action: str, phase: str) -> str:
    try:
        notes = input(f"  Notes for {action}/{phase} (optional, Enter to skip): ")
        return notes.strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def print_segment_result(info: dict):
    print(f"  ✅ Saved: {info['filename']}")
    print(f"     {info['byte_count']} bytes, "
          f"{info['frame_count']} frames "
          f"({info['broadcast_count']} broadcast, "
          f"{info['command_candidate_count']} non-broadcast), "
          f"{info['duration_s']:.1f}s")


def load_manifest(out_dir: str) -> dict | None:
    path = os.path.join(out_dir, "session_manifest.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError):
        return None
    return None


def build_completed_map(segments: list[dict]) -> dict[tuple[str, str], str]:
    completed: dict[tuple[str, str], str] = {}
    for seg in segments:
        action = seg.get("action")
        phase = seg.get("phase")
        filename = seg.get("filename", "")
        if isinstance(action, str) and isinstance(phase, str):
            completed[(action, phase)] = filename if isinstance(filename, str) else ""
    return completed


def next_segment_counter(out_dir: str, segments: list[dict]) -> int:
    max_idx = -1
    for seg in segments:
        filename = seg.get("filename", "")
        if not isinstance(filename, str):
            continue
        m = re.match(r"^(\d+)_.*\.bin$", filename)
        if m:
            max_idx = max(max_idx, int(m.group(1)))
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            m = re.match(r"^(\d+)_.*\.bin$", name)
            if m:
                max_idx = max(max_idx, int(m.group(1)))
    return max_idx + 1


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 4 — Command frame capture for Joyonway P25B85",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                                  # Full guided command capture
  %(prog)s --host 192.168.1.34 --port 8899  # Custom bridge address
  %(prog)s --dry-run                        # Simulate without connecting
  %(prog)s --actions cmd_pump_on,cmd_light_on  # Specific commands only
  %(prog)s --duration 15                    # 15s capture windows

Actions available:
  cmd_pump_on    — pump OFF → low
  cmd_pump_high  — pump low → high
  cmd_pump_off   — pump high → OFF
  cmd_light_on   — light OFF → ON
  cmd_light_off  — light ON → OFF
  cmd_temp_up    — temperature setpoint +1
  cmd_temp_down  — temperature setpoint -1
""",
    )
    parser.add_argument(
        "--host", default=_DEFAULT_HOST,
        help=f"RS485 bridge IP address (default: {_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help=f"RS485 bridge TCP port (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--duration", type=float, default=15.0,
        help="Capture duration per segment in seconds (default: 15). "
             "Longer = more bus cycles = better chance of catching the command frame.",
    )
    parser.add_argument(
        "--actions",
        help="Comma-separated list of actions, or 'all' (default: all). "
             "Available: " + ", ".join(a for a, _ in PHASE4_ACTIONS),
    )
    parser.add_argument(
        "--out-dir", default="./captures_phase4",
        help="Output directory (default: ./captures_phase4)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate capture without connecting to bridge",
    )
    return parser.parse_args()


def select_actions(actions_arg: str | None) -> list[tuple[str, str]]:
    if actions_arg is None or actions_arg.strip().lower() == "all":
        return PHASE4_ACTIONS

    action_map = {name: desc for name, desc in PHASE4_ACTIONS}
    selected = []
    for name in actions_arg.split(","):
        name = name.strip().lower()
        if name in action_map:
            selected.append((name, action_map[name]))
        else:
            print(f"⚠️  Unknown action '{name}', skipping. "
                  f"Available: {', '.join(action_map)}")
    return selected


def main():
    args = parse_args()
    actions = select_actions(args.actions)

    if not actions:
        print("No valid actions selected. Exiting.")
        sys.exit(1)

    print_banner()
    print_bridge_warning()

    if args.dry_run:
        print("🧪 DRY-RUN MODE — no real TCP connection will be made\n")

    print(f"Bridge:    {args.host}:{args.port}")
    print(f"Duration:  {args.duration}s per segment (press button ~3s in)")
    print(f"Output:    {os.path.abspath(args.out_dir)}")
    print(f"Actions:   {len(actions)} — {', '.join(a for a, _ in actions)}")
    print()

    existing_manifest = load_manifest(args.out_dir)
    existing_segments: list[dict] = []
    existing_started_at: str | None = None
    resume_mode = False

    if existing_manifest:
        segs = existing_manifest.get("segments", [])
        if isinstance(segs, list):
            existing_segments = [s for s in segs if isinstance(s, dict)]
        session_meta = existing_manifest.get("session", {})
        if isinstance(session_meta, dict):
            started_at = session_meta.get("started_at")
            if isinstance(started_at, str):
                existing_started_at = started_at

    completed_map = build_completed_map(existing_segments)
    plan = [(a, p) for a, _ in actions for p in PHASES]
    remaining = [(a, p) for a, p in plan if (a, p) not in completed_map]

    if existing_segments and remaining:
        resume_mode = True
        next_action, next_phase = remaining[0]
        print(f"Found existing manifest with {len(existing_segments)} captured segment(s).")
        print(f"Auto-resume: next step is {next_action}/{next_phase}.")
        print()
        if not prompt_continue("Press Enter to resume (Ctrl-C to abort)... "):
            print("\nAborted.")
            sys.exit(0)
    elif existing_segments and not remaining:
        print("All requested actions already captured. Nothing to do.")
        sys.exit(0)
    else:
        if not prompt_continue("Press Enter to begin Phase 4 capture (Ctrl-C to abort)... "):
            print("\nAborted.")
            sys.exit(0)

    session = CaptureSession(
        args.host,
        args.port,
        args.out_dir,
        args.dry_run,
        existing_segments=existing_segments,
        started_at=existing_started_at,
        segment_counter=next_segment_counter(args.out_dir, existing_segments),
        resumed=resume_mode,
    )
    completed_map = build_completed_map(session.segments)

    def signal_handler(sig, frame):
        session.mark_interrupted()
        print("\n\n⚠️  Interrupted! Saving manifest...")
        manifest_path = session.save_manifest()
        print(f"Manifest saved: {manifest_path}")
        sys.exit(130)

    signal.signal(signal.SIGINT, signal_handler)

    print()
    for action_idx, (action_name, action_desc) in enumerate(actions):
        print(f"\n{'━' * 66}")
        print(f"  Command {action_idx + 1}/{len(actions)}: {action_name}")
        print(f"  {action_desc}")
        print(f"{'━' * 66}")

        for phase in PHASES:
            if (action_name, phase) in completed_map:
                existing_file = completed_map[(action_name, phase)]
                msg = f"  ⏭  Skipping {action_name}/{phase}"
                if existing_file:
                    msg += f" (already captured: {existing_file})"
                print(f"\n{msg}")
                continue

            print(f"\n  Phase: {phase.upper()}")
            print(f"  {PHASE_INSTRUCTIONS[phase]}")

            if phase == "press":
                print()
                print("     ┌─────────────────────────────────────────────┐")
                print("     │  🕐 After you press Enter:                  │")
                print("     │     Wait ~3 seconds...                      │")
                print("     │     Then press the button ONCE              │")
                print("     │     Then wait for capture to finish         │")
                print("     └─────────────────────────────────────────────┘")

            if not prompt_continue(f"  Press Enter to capture {action_name}/{phase} "
                                   f"({args.duration}s)... "):
                session.mark_interrupted()
                break

            notes = prompt_notes(action_name, phase)

            if phase == "press":
                print(f"  ⏺  Capturing for {args.duration}s — PRESS THE BUTTON NOW (wait ~3s)...",
                      end="", flush=True)
            else:
                print(f"  ⏺  Capturing baseline for {args.duration}s...", end="", flush=True)

            try:
                info = session.capture_phase(action_name, phase, args.duration, notes)
            except (OSError, socket.error) as err:
                print(f"\n  ❌ Connection error: {err}")
                print("  Check bridge connectivity and retry.")
                session.mark_interrupted()
                break
            print(" done!")
            print_segment_result(info)
            completed_map[(action_name, phase)] = info["filename"]

        if session.interrupted:
            break

    # Save manifest
    print(f"\n{'━' * 66}")
    manifest_path = session.save_manifest()
    if session.interrupted:
        print(f"\n⚠️  Session interrupted. {len(session.segments)} segments captured.")
    else:
        print(f"\n✅ Phase 4 complete! {len(session.segments)} segments captured.")
    print(f"   Manifest: {manifest_path}")
    print(f"   Output:   {os.path.abspath(args.out_dir)}")
    print()
    print("Next steps:")
    print("  1. Diff baseline vs press captures to isolate command frames:")
    print(f"     python3 tools/frame_parser_38400.py --diff \\")
    print(f"       {args.out_dir}/00_cmd_pump_on_baseline.bin \\")
    print(f"       {args.out_dir}/01_cmd_pump_on_press.bin")
    print()
    print("  2. Non-broadcast frames in 'press' captures are command candidates.")
    print("     Look for short frames addressed to a specific device (not 0xFF).")
    print()


if __name__ == "__main__":
    main()

