#!/usr/bin/env python3
"""
Phase 5 — Extended command frame capture for Joyonway P25B85.

Captures RS485 bus traffic for functionality NOT yet captured in Phase 4:
  - Heater manual ON/OFF (from PB554 panel menu)
  - Disinfection (ozone/UV) manual toggle
  - Filtration schedule changes (timed filtration)

Phase 4 already captured: pump cycle, light toggle, temperature setpoint.

For each action we capture TWO segments:
  1. baseline — idle bus, no button pressed (reference)
  2. press    — button pressed during capture (contains command frame)

Additionally, for state-reading actions (where we need to observe broadcast
byte changes), we capture a THIRD segment:
  3. observe  — bus traffic AFTER the state change, to see new byte values

Priority groups:
  GROUP 1 (high priority — directly useful for HA entities):
    - Heater manual ON / OFF
    - Disinfection (ozone) manual ON / OFF

  GROUP 2 (medium priority — useful for automation):
    - Filtration manual ON / OFF (separate from pump jets)

  GROUP 3 (lower priority — nice to have, capture if time allows):
    - Filtration schedule programming (timed filtration)
    - Timed heating programming
    - Frost protection toggle
    - Screen flip (diagnostic only)

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

__version__ = "1.0.0-phase5"

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


def _get_default_port() -> int:
    """Read SPA_BRIDGE_PORT safely and fall back to default if invalid."""
    raw = os.environ.get("SPA_BRIDGE_PORT", "8899").strip()
    try:
        return int(raw)
    except ValueError:
        print(
            f"WARNING: invalid SPA_BRIDGE_PORT='{raw}', using default 8899.",
            file=sys.stderr,
        )
        return 8899


_DEFAULT_PORT = _get_default_port()

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

# Pseudo-escape table
ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}

# ──────────────────────────────────────────────────────────────
# Action definitions — grouped by priority
# ──────────────────────────────────────────────────────────────

# Each action: (name, description, phases, group)
# phases: list of phase names for this action

# Group 1 — High priority: heater and disinfection control
GROUP1_ACTIONS = [
    ("cmd_heater_on",
     "HEATER MANUAL ON — navigate PB554 menu to manually START heating.\n"
     "     On PB554: press the navigation button to enter the menu,\n"
     "     find the heating/heater option, and activate it.\n"
     "     Wait until display shows heating is active.",
     ["baseline", "press", "observe"]),

    ("cmd_heater_off",
     "HEATER MANUAL OFF — navigate PB554 menu to manually STOP heating.\n"
     "     Prerequisite: heater must be currently ON (from previous step).\n"
     "     Navigate the PB554 menu to deactivate/stop the heater.\n"
     "     Wait until display shows heating has stopped.",
     ["baseline", "press", "observe"]),

    ("cmd_disinfection_on",
     "DISINFECTION (OZONE/UV) ON — manually start disinfection cycle.\n"
     "     On PB554: navigate to the ozone/disinfection/UV option in the menu\n"
     "     and set it to ON or MANUAL.\n"
     "     Confirm the disinfection indicator appears on the display.",
     ["baseline", "press", "observe"]),

    ("cmd_disinfection_off",
     "DISINFECTION (OZONE/UV) OFF — manually stop disinfection cycle.\n"
     "     Prerequisite: disinfection must be currently ON.\n"
     "     Navigate the PB554 menu to deactivate/stop disinfection.\n"
     "     Confirm the disinfection indicator disappears from the display.",
     ["baseline", "press", "observe"]),
]

# Group 2 — Medium priority: filtration control
GROUP2_ACTIONS = [
    ("cmd_filtration_on",
     "FILTRATION MANUAL ON — start filtration pump manually.\n"
     "     This may be different from the pump button (which is pump low/high).\n"
     "     If filtration is controlled via the same pump button, skip this.\n"
     "     If there's a separate filtration menu option, use that.",
     ["baseline", "press", "observe"]),

    ("cmd_filtration_off",
     "FILTRATION MANUAL OFF — stop filtration pump manually.\n"
     "     Prerequisite: filtration must be currently running.\n"
     "     If filtration is the same as pump low, skip this.",
     ["baseline", "press", "observe"]),
]

# Group 3 — Lower priority: scheduling and configuration
GROUP3_ACTIONS = [
    ("cmd_filter_schedule_1",
     "FILTRATION SCHEDULE — program filtration timer slot 1.\n"
     "     On PB554: navigate to the filtration schedule/timer settings\n"
     "     and SET or CHANGE a filtration time window.\n"
     "     We want to capture the frame that programs a schedule slot.",
     ["baseline", "press"]),

    ("cmd_heat_schedule_1",
     "HEATING SCHEDULE — program timed heating slot 1.\n"
     "     On PB554: navigate to the timed heating settings\n"
     "     and SET or CHANGE a heating time window.",
     ["baseline", "press"]),

    ("cmd_frost_protect_on",
     "FROST PROTECTION ON — enable frost protection mode.\n"
     "     On PB554: navigate to the frost protection setting\n"
     "     and enable it. If already enabled, skip this action.",
     ["baseline", "press", "observe"]),

    ("cmd_frost_protect_off",
     "FROST PROTECTION OFF — disable frost protection mode.\n"
     "     Prerequisite: frost protection must be currently ON.\n"
     "     Navigate the PB554 menu to disable frost protection.",
     ["baseline", "press", "observe"]),

    ("cmd_screen_flip",
     "SCREEN FLIP — flip PB554 display 180 degrees.\n"
     "     Press the screen flip shortcut button on the PB554.\n"
     "     This is a diagnostic capture — low priority.",
     ["baseline", "press"]),
]

ALL_GROUPS = [
    ("Group 1 — High priority (heater + disinfection)", GROUP1_ACTIONS),
    ("Group 2 — Medium priority (filtration control)", GROUP2_ACTIONS),
    ("Group 3 — Lower priority (schedules + config)", GROUP3_ACTIONS),
]

PHASE_INSTRUCTIONS = {
    "baseline": (
        "Do NOT touch any buttons. Just let the bus idle for reference.\n"
        "     This captures the broadcast frames showing the CURRENT state."
    ),
    "press": (
        "⚡ Perform the action DURING this capture!\n"
        "     Wait ~3 seconds after capture starts, then do the action.\n"
        "     Do it only ONCE — we need a clean single command frame."
    ),
    "observe": (
        "Do NOT touch any buttons. Let the bus run AFTER the state change.\n"
        "     This captures the broadcast frames showing the NEW state,\n"
        "     so we can identify which bytes changed."
    ),
}


# ──────────────────────────────────────────────────────────────
# Frame analysis helpers
# ──────────────────────────────────────────────────────────────

def pseudo_unescape(data: bytes) -> bytes:
    """Reverse pseudo-escape encoding within a byte sequence."""
    result = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == ESCAPE_BYTE and i + 1 < n:
            suffix = data[i + 1]
            if suffix in ESCAPE_MAP:
                result.append(ESCAPE_MAP[suffix])
                i += 2
                continue
        result.append(data[i])
        i += 1
    return bytes(result)


def find_frames(data: bytes) -> list[bytes]:
    """Extract all complete frames from raw bytes."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frames.append(data[i : j + 1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def classify_frame(frame: bytes) -> str:
    """Classify a frame as broadcast, panel-to-controller, or other."""
    if len(frame) > 1:
        if frame[1] == 0xFF:
            return "broadcast"
        if frame[1] == 0x01 and len(frame) > 2 and frame[2] == 0x20:
            return "panel_cmd"  # panel (0x20) sending to controller (0x01)
        if frame[1] == 0x20:
            return "to_panel"   # controller sending to panel
    return "other"


def count_frames(data: bytes) -> tuple[int, int, int]:
    """Count total, broadcast, and non-broadcast frames."""
    frames = find_frames(data)
    broadcasts = sum(1 for f in frames if f[1] == 0xFF)
    return len(frames), broadcasts, len(frames) - broadcasts


def extract_command_frames(data: bytes) -> list[bytes]:
    """Extract non-broadcast frames (potential commands)."""
    return [f for f in find_frames(data) if len(f) > 1 and f[1] != 0xFF]


def analyze_broadcast_state(data: bytes) -> dict | None:
    """Extract key state values from the first broadcast frame."""
    for frame in find_frames(data):
        if len(frame) > 1 and frame[1] == 0xFF:
            # Unescape the frame interior
            unesc = frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
            if len(unesc) < 30:
                continue
            return {
                "water_temp_f": unesc[9],
                "water_temp_c": round((unesc[9] - 32) * 5 / 9),
                "pump_byte": f"0x{unesc[12]:02X}",
                "heater_byte": f"0x{unesc[14]:02X}",
                "setpoint_f": unesc[16],
                "setpoint_c": round((unesc[16] - 32) * 5 / 9),
                "light_byte": f"0x{unesc[17]:02X}",
                "activity_byte": f"0x{unesc[28]:02X}" if len(unesc) > 28 else "N/A",
            }
    return None


def diff_broadcast_states(before: dict | None, after: dict | None) -> list[str]:
    """Compare two broadcast state dicts and return list of changes."""
    if not before or not after:
        return ["(cannot diff — missing broadcast data)"]
    changes = []
    for key in before:
        if before[key] != after[key]:
            changes.append(f"  {key}: {before[key]} → {after[key]}")
    return changes if changes else ["  (no changes detected)"]


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
    for _ in range(max(1, int(duration / 0.6))):
        result.extend(filler)
        result.extend(ref)
    return bytes(result)


# ──────────────────────────────────────────────────────────────
# Session management
# ──────────────────────────────────────────────────────────────

class CaptureSession:
    """Manages the Phase 5 capture session."""

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
        broadcast_state = analyze_broadcast_state(data)

        # Save raw data
        with open(filepath, "wb") as f:
            f.write(data)

        # Quick analysis: show non-broadcast frames found
        non_bc = extract_command_frames(data)

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
            "broadcast_state": broadcast_state,
            "notes": notes,
        }
        self.segments.append(segment_info)

        # Print immediate analysis
        if broadcast_state:
            print(f"\n     📊 Broadcast state: temp={broadcast_state['water_temp_c']}°C "
                  f"setpoint={broadcast_state['setpoint_c']}°C "
                  f"heater={broadcast_state['heater_byte']} "
                  f"pump={broadcast_state['pump_byte']} "
                  f"light={broadcast_state['light_byte']} "
                  f"activity={broadcast_state['activity_byte']}")

        if non_bc and phase == "press":
            print(f"\n     🔍 Found {len(non_bc)} non-broadcast frame(s) — potential commands:")
            for idx, fr in enumerate(non_bc[:10]):
                ftype = classify_frame(fr)
                print(f"        [{idx}] ({ftype}) {fr.hex()}")
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
                "phase": "phase5_extended_commands",
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
    print("=" * 70)
    print("  Joyonway Spa — Phase 5: Extended Command Frame Capture")
    print(f"  Version {__version__}")
    print("=" * 70)
    print()
    print("This tool captures COMMAND frames for functionality not yet captured:")
    print()
    print("  GROUP 1 (high priority):")
    print("    • Heater manual ON/OFF — control heating from HA")
    print("    • Disinfection (ozone/UV) manual ON/OFF")
    print()
    print("  GROUP 2 (medium priority):")
    print("    • Filtration manual ON/OFF (if separate from pump button)")
    print()
    print("  GROUP 3 (lower priority):")
    print("    • Filtration/heating schedule programming")
    print("    • Frost protection toggle")
    print("    • Screen flip")
    print()
    print("For each action, we capture baseline + press + observe segments.")
    print("The 'observe' segment captures broadcast bytes AFTER the state change,")
    print("so we can identify which bytes reflect the new state in the protocol.")
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


def prompt_continue(message: str = "Press Enter to continue...") -> bool:
    try:
        input(message)
        return True
    except (KeyboardInterrupt, EOFError):
        return False


def prompt_yes_no(message: str) -> bool | None:
    """Ask a yes/no/skip question. Returns True/False/None (on interrupt)."""
    try:
        while True:
            answer = input(f"{message} [y/n/skip]: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no", "s", "skip"):
                return False
            print("  Please answer with y, n, or skip.")
    except (KeyboardInterrupt, EOFError):
        return None


def prompt_notes(action: str, phase: str) -> str | None:
    try:
        notes = input(f"  Notes for {action}/{phase} (optional, Enter to skip): ")
        return notes.strip()
    except (KeyboardInterrupt, EOFError):
        return None


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
    all_action_names = []
    for _, actions in ALL_GROUPS:
        all_action_names.extend(a[0] for a in actions)

    parser = argparse.ArgumentParser(
        description="Phase 5 — Extended command frame capture for Joyonway P25B85",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Examples:
  %(prog)s                                         # Full guided capture (all groups)
  %(prog)s --group 1                               # Group 1 only (heater + disinfection)
  %(prog)s --group 1,2                             # Groups 1 and 2
  %(prog)s --actions cmd_heater_on,cmd_heater_off  # Specific actions only
  %(prog)s --host 192.168.1.34 --port 8899         # Custom bridge address
  %(prog)s --dry-run                               # Simulate without connecting
  %(prog)s --duration 20                           # 20s capture windows

Actions available:
  Group 1 (high priority):
    cmd_heater_on          — manually start heating
    cmd_heater_off         — manually stop heating
    cmd_disinfection_on    — manually start disinfection (ozone/UV)
    cmd_disinfection_off   — manually stop disinfection (ozone/UV)

  Group 2 (medium priority):
    cmd_filtration_on      — manually start filtration
    cmd_filtration_off     — manually stop filtration

  Group 3 (lower priority):
    cmd_filter_schedule_1  — program filtration timer
    cmd_heat_schedule_1    — program heating timer
    cmd_frost_protect_on   — enable frost protection
    cmd_frost_protect_off  — disable frost protection
    cmd_screen_flip        — flip display 180°
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
             "Use 20-25s for menu navigation actions that take longer.",
    )
    parser.add_argument(
        "--group",
        help="Comma-separated group numbers to capture (1, 2, 3, or 'all'). "
             "Default: all.",
    )
    parser.add_argument(
        "--actions",
        help="Comma-separated list of specific actions. Overrides --group.",
    )
    parser.add_argument(
        "--out-dir", default="./captures_phase5",
        help="Output directory (default: ./captures_phase5)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate capture without connecting to bridge",
    )
    return parser.parse_args()


def select_actions(args: argparse.Namespace) -> list[tuple[str, str, list[str]]]:
    """Select actions based on --actions or --group arguments.

    Returns list of (name, description, phases).
    """
    # Build lookup
    all_actions: dict[str, tuple[str, str, list[str]]] = {}
    for _, group_actions in ALL_GROUPS:
        for name, desc, phases in group_actions:
            all_actions[name] = (name, desc, phases)

    # --actions overrides --group
    if args.actions:
        selected = []
        for name in args.actions.split(","):
            name = name.strip().lower()
            if name in all_actions:
                selected.append(all_actions[name])
            else:
                print(f"⚠️  Unknown action '{name}', skipping. "
                      f"Available: {', '.join(all_actions)}")
        return selected

    # --group selection
    group_arg = args.group
    if group_arg is None or group_arg.strip().lower() == "all":
        group_nums = {1, 2, 3}
    else:
        group_nums = set()
        for g in group_arg.split(","):
            g = g.strip()
            if g.isdigit() and 1 <= int(g) <= 3:
                group_nums.add(int(g))
            else:
                print(f"⚠️  Invalid group '{g}', skipping. Use 1, 2, or 3.")

    selected = []
    for idx, (_, group_actions) in enumerate(ALL_GROUPS, 1):
        if idx in group_nums:
            selected.extend(group_actions)
    return selected


def run_action(
    session: CaptureSession,
    action_name: str,
    action_desc: str,
    phases: list[str],
    completed_map: dict[tuple[str, str], str],
    duration: float,
    action_idx: int,
    total_actions: int,
) -> bool:
    """Run capture for one action. Returns False if interrupted."""

    print(f"\n{'━' * 70}")
    print(f"  Action {action_idx}/{total_actions}: {action_name}")
    print(f"  {action_desc}")
    print(f"{'━' * 70}")

    # Check if this action exists on the PB554 panel
    answer = prompt_yes_no(
        f"\n  Does your PB554 panel have this function? "
    )
    if answer is None:
        session.mark_interrupted()
        return False
    if not answer:
        print(f"  ⏭  Skipping {action_name} — not available on panel.")
        return True

    baseline_state = None
    press_state = None

    for phase in phases:
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
            print("     ┌──────────────────────────────────────────────────────┐")
            print("     │  🕐 After you press Enter:                           │")
            print("     │     Wait ~3 seconds...                               │")
            print("     │     Then perform the action ONCE on the PB554 panel  │")
            print("     │     Then wait for capture to finish                  │")
            print("     └──────────────────────────────────────────────────────┘")

        if phase == "observe":
            print()
            print("     ┌──────────────────────────────────────────────────────┐")
            print("     │  👁  Just let the bus run — DO NOT press anything     │")
            print("     │     We're recording the state AFTER the change       │")
            print("     └──────────────────────────────────────────────────────┘")

        if not prompt_continue(f"  Press Enter to capture {action_name}/{phase} "
                               f"({duration}s)... "):
            session.mark_interrupted()
            return False

        notes = prompt_notes(action_name, phase)
        if notes is None:
            session.mark_interrupted()
            return False

        if phase == "press":
            print(f"  ⏺  Capturing for {duration}s — PERFORM THE ACTION NOW (wait ~3s)...",
                  end="", flush=True)
        elif phase == "observe":
            print(f"  ⏺  Observing new state for {duration}s...",
                  end="", flush=True)
        else:
            print(f"  ⏺  Capturing baseline for {duration}s...", end="", flush=True)

        try:
            info = session.capture_phase(action_name, phase, duration, notes)
        except (OSError, socket.error) as err:
            print(f"\n  ❌ Connection error: {err}")
            print("  Check bridge connectivity and retry.")
            session.mark_interrupted()
            return False
        print(" done!")
        print_segment_result(info)
        completed_map[(action_name, phase)] = info["filename"]

        # Track broadcast state for diffing
        state = info.get("broadcast_state")
        if phase == "baseline":
            baseline_state = state
        elif phase == "press":
            press_state = state

        # Show diff after observe phase
        if phase == "observe" and baseline_state:
            observe_state = state
            changes = diff_broadcast_states(baseline_state, observe_state)
            print(f"\n     📋 Broadcast changes (baseline → after action):")
            for c in changes:
                print(f"     {c}")

        # Also show diff after press vs baseline
        if phase == "press" and baseline_state and press_state:
            changes = diff_broadcast_states(baseline_state, press_state)
            if any("→" in c for c in changes):
                print(f"\n     📋 Broadcast changes (baseline → during press):")
                for c in changes:
                    print(f"     {c}")

    return True


def main():
    args = parse_args()
    actions = select_actions(args)

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
    print(f"Actions:   {len(actions)} — {', '.join(a[0] for a in actions)}")
    print()

    # Check for resume
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

    # Count remaining steps
    plan = [(a[0], p) for a in actions for p in a[2]]
    remaining = [(a, p) for a, p in plan if (a, p) not in completed_map]

    if existing_segments and remaining:
        resume_mode = True
        print(f"Found existing manifest with {len(existing_segments)} captured segment(s).")
        print(f"{len(remaining)} segment(s) remaining.")
        print()
        if not prompt_continue("Press Enter to resume (Ctrl-C to abort)... "):
            print("\nAborted.")
            sys.exit(0)
    elif existing_segments and not remaining:
        print("All requested actions already captured. Nothing to do.")
        sys.exit(0)
    else:
        if not prompt_continue("Press Enter to begin Phase 5 capture (Ctrl-C to abort)... "):
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
    for action_idx, (action_name, action_desc, phases) in enumerate(actions, 1):
        ok = run_action(
            session, action_name, action_desc, phases,
            completed_map, args.duration,
            action_idx, len(actions),
        )
        if not ok:
            break

    # Save manifest
    print(f"\n{'━' * 70}")
    manifest_path = session.save_manifest()
    if session.interrupted:
        print(f"\n⚠️  Session interrupted. {len(session.segments)} segments captured.")
    else:
        print(f"\n✅ Phase 5 complete! {len(session.segments)} segments captured.")
    print(f"   Manifest: {manifest_path}")
    print(f"   Output:   {os.path.abspath(args.out_dir)}")
    print()

    # Summary: show all captured states for quick byte comparison
    print("📊 Captured broadcast states summary:")
    print("─" * 70)
    for seg in session.segments:
        state = seg.get("broadcast_state")
        if state:
            print(f"  {seg['action']}/{seg['phase']}: "
                  f"heater={state['heater_byte']} "
                  f"pump={state['pump_byte']} "
                  f"light={state['light_byte']} "
                  f"activity={state['activity_byte']}")
    print()

    print("Next steps:")
    print("  1. Compare baseline vs press captures to isolate command frames:")
    print(f"     python3 tools/decode_phase4.py {args.out_dir}")
    print()
    print("  2. Compare baseline vs observe to identify broadcast byte changes")
    print("     for new states (heater on/off, disinfection, etc.).")
    print()
    print("  3. Add validated command frames to adapters/p25b85.py")
    print()


if __name__ == "__main__":
    main()

