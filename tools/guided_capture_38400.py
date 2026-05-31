#!/usr/bin/env python3
"""
Guided capture tool for Joyonway spa RS485 frames at 38400 baud via TCP bridge.

Walks you through a sequence of capture scenarios (baseline → action → baseline)
and saves raw .bin files + session manifest for later analysis with frame_parser_38400.py.

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

__version__ = "1.0.0"

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

# Default capture actions in recommended order
DEFAULT_ACTIONS = [
    ("baseline",  "Initial baseline — everything OFF on the spa panel"),
    ("light_on",  "Light ON — press light button (any color)"),
    ("pump_low",  "Pump LOW — press pump button once (filtration/circulation)"),
    ("pump_high", "Pump HIGH — press pump button again (massage jets)"),
    ("heater",    "Heater active — raise setpoint above current water temp"),
    ("uv_lamp",   "UV lamp — activate UV/ozone if accessible from panel"),
    ("setpoint",  "Setpoint change — try 2–3 different °F values"),
]

# Each action is captured in 3 phases
PHASES = ["before", "active", "after"]
PHASE_INSTRUCTIONS = {
    "before": "Ensure the spa is in BASELINE state (everything OFF) before we start.",
    "active": "Now perform the action described above and keep it active.",
    "after":  "Return the spa to BASELINE state (everything OFF).",
}

# ──────────────────────────────────────────────────────────────
# Frame counting helpers
# ──────────────────────────────────────────────────────────────

def count_frames(data: bytes) -> tuple[int, int]:
    """Count total frames and broadcast frames in raw data.

    Returns (frame_count, broadcast_count).
    """
    frames = 0
    broadcasts = 0
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            # Find the next FRAME_END that isn't part of an escape sequence
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    # Check it's not escaped (preceded by 0x1B with escape byte 0x14)
                    # But in raw stream, 0x1D as end delimiter is NOT escaped —
                    # only interior 0x1D values are escaped as 0x1B 0x14.
                    # So any bare 0x1D is a real end delimiter.
                    frame = data[i : j + 1]
                    frames += 1
                    if len(frame) > 1 and frame[1] == 0xFF:
                        broadcasts += 1
                    i = j + 1
                    break
                j += 1
            else:
                # No end delimiter found — partial frame
                break
        else:
            i += 1
    return frames, broadcasts


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
    """Simulate a capture for dry-run mode using KDy's reference frame."""
    # KDy reference P25B85 broadcast frame
    ref = bytes.fromhex(
        "1aff013cd2b4ff08035e040604f540006801001221123b"
        "1400160004004300043b120014000000064d0000000000"
        "000000000000001005081b1b111200004e28331d"
    )
    # Simulate a few poll/response + broadcast cycles
    filler = bytes.fromhex("1a100103000000001d")  # fake short poll frame
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
    """Manages the capture session state and manifest."""

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
        """Capture one phase segment and save to disk."""
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

        frame_count, broadcast_count = count_frames(data)

        # Save raw data
        with open(filepath, "wb") as f:
            f.write(data)

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
            "notes": notes,
        }
        self.segments.append(segment_info)
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
    print("  Joyonway Spa — Guided RS485 Capture Tool (38400 baud)")
    print(f"  Version {__version__}")
    print("=" * 66)
    print()
    print("This tool captures raw RS485 frames from your spa controller")
    print("via a TCP bridge (e.g. Elfin EW11, USR-W610).")
    print()
    print("For each action, three segments are recorded:")
    print("  1. BEFORE  — baseline with spa idle / equipment OFF")
    print("  2. ACTIVE  — the action is happening")
    print("  3. AFTER   — return to baseline")
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
    """Prompt user to continue. Returns False if Ctrl-C / EOF."""
    try:
        input(message)
        return True
    except (KeyboardInterrupt, EOFError):
        return False


def prompt_notes(action: str, phase: str) -> str:
    """Ask user for optional notes about this segment."""
    try:
        notes = input(f"  Notes for {action}/{phase} (optional, Enter to skip): ")
        return notes.strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def print_segment_result(info: dict):
    """Display capture segment summary."""
    print(f"  ✅ Saved: {info['filename']}")
    print(f"     {info['byte_count']} bytes, "
          f"{info['frame_count']} frames, "
          f"{info['broadcast_count']} broadcast frames, "
          f"{info['duration_s']:.1f}s")


def _manifest_path(out_dir: str) -> str:
    return os.path.join(out_dir, "session_manifest.json")


def load_manifest(out_dir: str) -> dict | None:
    """Load existing session_manifest.json if present and valid JSON."""
    path = _manifest_path(out_dir)
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
    """Map (action, phase) to filename for already-captured segments."""
    completed: dict[tuple[str, str], str] = {}
    for seg in segments:
        action = seg.get("action")
        phase = seg.get("phase")
        filename = seg.get("filename", "")
        if isinstance(action, str) and isinstance(phase, str):
            completed[(action, phase)] = filename if isinstance(filename, str) else ""
    return completed


def next_segment_counter(out_dir: str, segments: list[dict]) -> int:
    """Find the next numeric prefix for segment filenames."""
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
        description="Guided RS485 capture tool for Joyonway spa controllers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s                                  # Interactive capture with defaults
  %(prog)s --host 192.168.1.34 --port 8899  # Custom bridge address
  %(prog)s --dry-run                        # Simulate without connecting
  %(prog)s --actions light_on,pump_low      # Capture specific actions only
  %(prog)s --duration 15 --out-dir my_caps  # 15s segments, custom output dir
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
        "--duration", type=float, default=10.0,
        help="Capture duration per segment in seconds (default: 10)",
    )
    parser.add_argument(
        "--actions",
        help="Comma-separated list of actions to capture, or 'all' (default: all). "
             "Available: " + ", ".join(a for a, _ in DEFAULT_ACTIONS),
    )
    parser.add_argument(
        "--out-dir", default="./captures",
        help="Output directory for .bin files and manifest (default: ./captures)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate capture without connecting to bridge",
    )
    return parser.parse_args()


def select_actions(actions_arg: str | None) -> list[tuple[str, str]]:
    """Parse --actions argument into list of (name, description) tuples."""
    if actions_arg is None or actions_arg.strip().lower() == "all":
        return DEFAULT_ACTIONS

    action_map = {name: desc for name, desc in DEFAULT_ACTIONS}
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
    print(f"Duration:  {args.duration}s per segment")
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
        print(f"Auto-resume enabled: next step is {next_action}/{next_phase}.")
        print()
        if not prompt_continue("Press Enter to resume capture session (Ctrl-C to abort)... "):
            print("\nAborted.")
            sys.exit(0)
    elif existing_segments and not remaining:
        print("All requested action/phase segments are already captured in this output directory.")
        print("Nothing to do.")
        sys.exit(0)
    else:
        if not prompt_continue("Press Enter to begin the capture session (Ctrl-C to abort)... "):
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

    # Handle Ctrl-C gracefully — save manifest with what we have
    def signal_handler(sig, frame):
        session.mark_interrupted()
        print("\n\n⚠️  Interrupted! Saving manifest with captured segments...")
        manifest_path = session.save_manifest()
        print(f"Manifest saved: {manifest_path}")
        sys.exit(130)

    signal.signal(signal.SIGINT, signal_handler)

    print()
    for action_idx, (action_name, action_desc) in enumerate(actions):
        print(f"\n{'━' * 66}")
        print(f"  Action {action_idx + 1}/{len(actions)}: {action_name}")
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

            if not prompt_continue(f"  Press Enter to capture {action_name}/{phase} "
                                   f"({args.duration}s)... "):
                session.mark_interrupted()
                break

            notes = prompt_notes(action_name, phase)

            print(f"  ⏺  Capturing for {args.duration}s...", end="", flush=True)
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
        print(f"\n⚠️  Session interrupted. {len(session.segments)} segments captured so far.")
    else:
        print(f"\n✅ Session complete! {len(session.segments)} segments captured.")
    print(f"   Manifest: {manifest_path}")
    print(f"   Output:   {os.path.abspath(args.out_dir)}")
    print()
    print("Next step: analyze captures with frame_parser_38400.py")
    print(f"  python3 tools/frame_parser_38400.py {args.out_dir}/*.bin")
    print()


if __name__ == "__main__":
    main()

