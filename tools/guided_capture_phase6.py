#!/usr/bin/env python3
"""
Phase 6 — Full functionality capture for Joyonway P25B85.

Captures RS485 bus traffic for ALL implemented HA entities and known uncaptured
functionality. Designed for use at the spa with flexible timing:
  - Baseline captures are fixed 10s
  - Action captures wait for Enter (start) → Enter (stop), with live timer

Supports:
  - Resumable sessions (saves manifest after each segment)
  - Ad-hoc captures for newly discovered functions (interactive naming)
  - Ctrl-C graceful exit with manifest save

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
import threading
import time

__version__ = "1.0.0-phase6"

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
    raw = os.environ.get("SPA_BRIDGE_PORT", "8899").strip()
    try:
        return int(raw)
    except ValueError:
        print(f"WARNING: invalid SPA_BRIDGE_PORT='{raw}', using default 8899.", file=sys.stderr)
        return 8899


_DEFAULT_PORT = _get_default_port()

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}

BASELINE_DURATION = 10.0  # seconds for baseline captures

# ──────────────────────────────────────────────────────────────
# Action definitions
# ──────────────────────────────────────────────────────────────

# Each action: (name, description, instruction, has_observe)
# has_observe: whether to capture a post-action observe segment

PREDEFINED_ACTIONS = [
    # --- HA entity verification ---
    ("light_toggle_on",
     "Light ON — toggle from OFF to ON",
     "Press the LIGHT button on PB554 panel (light should turn ON).",
     True),

    ("light_toggle_off",
     "Light OFF — toggle from ON to OFF",
     "Press the LIGHT button on PB554 panel (light should turn OFF).",
     True),

    ("pump_off_to_low",
     "Pump OFF → LOW (filtration)",
     "Press the PUMP button once on PB554 (starts filtration/low speed).",
     True),

    ("pump_low_to_high",
     "Pump LOW → HIGH (massage jets)",
     "Press the PUMP button again on PB554 (switches to high speed jets).",
     True),

    ("pump_high_to_off",
     "Pump HIGH → OFF",
     "Press the PUMP button on PB554 (pump turns off from high).",
     True),

    ("blower_on",
     "Blower ON",
     "Press the BLOWER button on PB554 (blower should activate).",
     True),

    ("blower_off",
     "Blower OFF",
     "Press the BLOWER button on PB554 (blower should stop).",
     True),

    ("heater_on",
     "Heater ON — raise setpoint above water temp",
     "On PB554, raise the temperature setpoint ABOVE current water temp.\n"
     "     The heater should start heating.",
     True),

    ("heater_off",
     "Heater OFF — lower setpoint below water temp",
     "On PB554, lower the temperature setpoint BELOW current water temp.\n"
     "     The heater should stop.",
     True),

    ("setpoint_up",
     "Temperature setpoint +1°F",
     "On PB554, increase the setpoint by 1 degree.",
     False),

    ("setpoint_down",
     "Temperature setpoint -1°F",
     "On PB554, decrease the setpoint by 1 degree.",
     False),

    # --- Schedules ---
    ("heat_schedule_change",
     "Heat schedule — change a time slot",
     "On PB554, navigate to heat schedule settings and CHANGE one time slot\n"
     "     (e.g. set slot 1 start to a different hour).",
     True),

    ("heat_schedule_enable",
     "Heat schedule — enable a slot",
     "On PB554, ENABLE a previously disabled heat schedule slot.",
     True),

    ("heat_schedule_disable",
     "Heat schedule — disable a slot",
     "On PB554, DISABLE an active heat schedule slot.",
     True),

    ("filter_schedule_change",
     "Filter schedule — change a time slot",
     "On PB554, navigate to filter schedule settings and CHANGE one time slot.",
     True),

    ("filter_schedule_enable",
     "Filter schedule — enable a slot",
     "On PB554, ENABLE a previously disabled filter schedule slot.",
     True),

    ("filter_schedule_disable",
     "Filter schedule — disable a slot",
     "On PB554, DISABLE an active filter schedule slot.",
     True),

    # --- DateTime ---
    ("datetime_set",
     "Set date/time on panel",
     "On PB554, navigate to date/time settings and SET a new date + time.\n"
     "     Take your time — capture will wait for you to finish.",
     True),

    # --- Uncaptured functionality ---
    ("ozone_mode_auto",
     "Ozone — set mode to Auto",
     "On PB554, set ozone/disinfection mode to AUTO (scheduled).",
     True),

    ("ozone_mode_manual",
     "Ozone — set mode to Manual",
     "On PB554, set ozone/disinfection mode to MANUAL.",
     True),

    ("ozone_manual_on",
     "Ozone — manual ON",
     "With ozone in Manual mode, ACTIVATE ozone from the panel.",
     True),

    ("ozone_manual_off",
     "Ozone — manual OFF",
     "With ozone in Manual mode, DEACTIVATE ozone from the panel.",
     True),

    # --- Panel settings (likely panel-local, but let's verify) ---
    ("panel_auto_lock",
     "Panel auto-lock setting",
     "Toggle the Auto Lock setting on PB554.",
     False),

    ("panel_brightness",
     "Panel brightness change",
     "Change the screen brightness on PB554.",
     False),

    ("panel_screen_flip",
     "Panel screen flip",
     "Flip the PB554 display 180°.",
     False),

    ("light_mode_change",
     "Light mode — change color mode",
     "On PB554, change the light mode/color cycling setting.",
     True),
]

ALL_ACTION_NAMES = [a[0] for a in PREDEFINED_ACTIONS]


# ──────────────────────────────────────────────────────────────
# Frame analysis helpers
# ──────────────────────────────────────────────────────────────

def pseudo_unescape(data: bytes) -> bytes:
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
    if len(frame) > 1:
        if frame[1] == 0xFF:
            return "broadcast"
        if frame[1] == 0x01 and len(frame) > 2 and frame[2] == 0x20:
            return "panel_cmd"
        if frame[1] == 0x20:
            return "to_panel"
    return "other"


def count_frames(data: bytes) -> tuple[int, int, int]:
    frames = find_frames(data)
    broadcasts = sum(1 for f in frames if len(f) > 1 and f[1] == 0xFF)
    return len(frames), broadcasts, len(frames) - broadcasts


def extract_command_frames(data: bytes) -> list[bytes]:
    return [f for f in find_frames(data) if len(f) > 1 and f[1] != 0xFF]


def analyze_broadcast_state(data: bytes) -> dict | None:
    for frame in find_frames(data):
        if len(frame) > 1 and frame[1] == 0xFF:
            unesc = frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
            if len(unesc) < 30:
                continue
            result = {
                "water_temp_f": unesc[9],
                "water_temp_c": round((unesc[9] - 32) * 5 / 9),
                "pump_byte": f"0x{unesc[12]:02X}",
                "heater_byte": f"0x{unesc[14]:02X}",
                "setpoint_f": unesc[16],
                "setpoint_c": round((unesc[16] - 32) * 5 / 9),
                "light_byte": f"0x{unesc[17]:02X}",
                "activity_byte": f"0x{unesc[28]:02X}" if len(unesc) > 28 else "N/A",
            }
            if len(unesc) > 36:
                result["heat_sched_raw"] = unesc[19:27].hex()
                result["filter_sched_raw"] = unesc[29:37].hex()
            if len(unesc) > 58:
                result["clock_raw"] = unesc[53:59].hex()
            return result
    return None


def diff_broadcast_states(before: dict | None, after: dict | None) -> list[str]:
    if not before or not after:
        return ["(cannot diff — missing broadcast data)"]
    changes = []
    for key in before:
        if before[key] != after[key]:
            changes.append(f"  {key}: {before[key]} → {after[key]}")
    return changes if changes else ["  (no changes detected)"]


# ──────────────────────────────────────────────────────────────
# TCP capture — timed and interactive
# ──────────────────────────────────────────────────────────────

def capture_timed(host: str, port: int, duration: float, timeout: float = 10.0) -> bytes:
    """Capture for a fixed duration."""
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


def capture_until_enter(host: str, port: int, timeout: float = 10.0) -> tuple[bytes, float]:
    """
    Capture until user presses Enter. Shows a live timer in the terminal.
    Returns (data, duration_seconds).
    """
    sock = socket.create_connection((host, port), timeout=timeout)
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

    # Live timer display
    try:
        while True:
            elapsed = time.time() - start_time
            print(f"\r     ⏱  Recording... {elapsed:.0f}s (press Enter to stop)", end="", flush=True)
            # Check for Enter with a short timeout using select-like approach
            import select
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
    print(f"\r     ⏱  Recording stopped after {duration:.1f}s" + " " * 20)
    return bytes(buf), duration


def dry_run_capture_timed(duration: float) -> bytes:
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


def dry_run_capture_interactive() -> tuple[bytes, float]:
    """Simulate interactive capture in dry-run mode."""
    print(f"\r     ⏱  [DRY-RUN] Press Enter to stop simulated capture...", end="", flush=True)
    try:
        input()
    except (KeyboardInterrupt, EOFError):
        pass
    duration = 5.0
    print(f"\r     ⏱  [DRY-RUN] Simulated {duration:.1f}s capture" + " " * 20)
    return dry_run_capture_timed(duration), duration


# ──────────────────────────────────────────────────────────────
# Session management
# ──────────────────────────────────────────────────────────────

class CaptureSession:
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

    def capture_baseline(self, action: str, notes: str = "") -> dict:
        """Capture a fixed-duration baseline segment (10s)."""
        return self._do_capture(action, "baseline", BASELINE_DURATION, notes=notes)

    def capture_action(self, action: str, notes: str = "") -> dict:
        """Capture an action segment — waits for Enter to stop."""
        return self._do_interactive_capture(action, "press", notes=notes)

    def capture_observe(self, action: str, notes: str = "") -> dict:
        """Capture a post-action observe segment (10s)."""
        return self._do_capture(action, "observe", BASELINE_DURATION, notes=notes)

    def _do_capture(
        self, action: str, phase: str, duration: float, notes: str = ""
    ) -> dict:
        filename = f"{self.segment_counter:02d}_{action}_{phase}.bin"
        filepath = os.path.join(self.out_dir, filename)
        self.segment_counter += 1

        start_time = datetime.datetime.now(datetime.timezone.utc)

        if self.dry_run:
            data = dry_run_capture_timed(duration)
        else:
            data = capture_timed(self.host, self.port, duration)

        end_time = datetime.datetime.now(datetime.timezone.utc)
        actual_duration = (end_time - start_time).total_seconds()

        return self._save_segment(
            action, phase, filename, filepath, data,
            start_time, end_time, actual_duration, notes,
        )

    def _do_interactive_capture(self, action: str, phase: str, notes: str = "") -> dict:
        filename = f"{self.segment_counter:02d}_{action}_{phase}.bin"
        filepath = os.path.join(self.out_dir, filename)
        self.segment_counter += 1

        start_time = datetime.datetime.now(datetime.timezone.utc)

        if self.dry_run:
            data, actual_duration = dry_run_capture_interactive()
        else:
            data, actual_duration = capture_until_enter(self.host, self.port)

        end_time = datetime.datetime.now(datetime.timezone.utc)

        return self._save_segment(
            action, phase, filename, filepath, data,
            start_time, end_time, actual_duration, notes,
        )

    def _save_segment(
        self, action, phase, filename, filepath, data,
        start_time, end_time, actual_duration, notes,
    ) -> dict:
        frame_count, broadcast_count, cmd_count = count_frames(data)
        broadcast_state = analyze_broadcast_state(data)

        with open(filepath, "wb") as f:
            f.write(data)

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

        # Auto-save manifest after every segment (resumable)
        self.save_manifest()

        # Print immediate analysis
        if broadcast_state:
            print(f"     📊 State: temp={broadcast_state['water_temp_c']}°C "
                  f"set={broadcast_state['setpoint_c']}°C "
                  f"heater={broadcast_state['heater_byte']} "
                  f"pump={broadcast_state['pump_byte']} "
                  f"light={broadcast_state['light_byte']} "
                  f"activity={broadcast_state['activity_byte']}")

        if non_bc and phase == "press":
            print(f"     🔍 Found {len(non_bc)} command candidate(s):")
            for idx, fr in enumerate(non_bc[:10]):
                ftype = classify_frame(fr)
                print(f"        [{idx}] ({ftype}) {fr.hex()}")
            if len(non_bc) > 10:
                print(f"        ... and {len(non_bc) - 10} more")

        return segment_info

    def save_manifest(self) -> str:
        manifest = {
            "session": {
                "started_at": self.started_at,
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "host": self.host,
                "port": self.port,
                "tool_version": __version__,
                "phase": "phase6_full_capture",
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
    print("  Joyonway Spa — Phase 6: Full Functionality Capture")
    print(f"  Version {__version__}")
    print("=" * 70)
    print()
    print("Captures RS485 traffic for ALL implemented and planned entities.")
    print()
    print("Capture flow per action:")
    print("  1. BASELINE — 10s idle recording (no interaction)")
    print("  2. PRESS    — you perform the action; recording runs until you")
    print("                press Enter (live timer shown)")
    print("  3. OBSERVE  — 10s post-action recording (optional, for state changes)")
    print()
    print("Ad-hoc captures: type 'custom' when offered to name a new action")
    print("for any functionality you discover at the spa.")
    print()


def print_bridge_info(host: str, port: int):
    print(f"Bridge:  {host}:{port}")
    print(f"Output:  captures_phase6/")
    print()
    print("⚠️  EW11 supports 4 TCP clients. HA can stay connected!")
    print("    (Unlike older scripts, no need to stop HA integration.)")
    print()


def prompt_continue(message: str = "Press Enter to continue...") -> bool:
    try:
        input(message)
        return True
    except (KeyboardInterrupt, EOFError):
        return False


def prompt_yes_no(message: str) -> bool | None:
    try:
        while True:
            answer = input(f"{message} [y/n]: ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no", "s", "skip"):
                return False
            print("  Please answer y or n.")
    except (KeyboardInterrupt, EOFError):
        return None


def prompt_notes(action: str) -> str | None:
    try:
        notes = input(f"  Notes (optional, Enter to skip): ")
        return notes.strip()
    except (KeyboardInterrupt, EOFError):
        return None


def prompt_custom_action() -> tuple[str, str] | None:
    """Ask user to name and describe a custom action."""
    try:
        print()
        print("  ┌─ Ad-hoc capture ─────────────────────────────────────────┐")
        print("  │  Name your discovery so we can analyze it later.          │")
        print("  └──────────────────────────────────────────────────────────┘")
        name = input("  Action name (snake_case, e.g. 'eco_mode_toggle'): ").strip()
        if not name:
            return None
        # Sanitize
        name = re.sub(r"[^a-z0-9_]", "_", name.lower())
        name = re.sub(r"_+", "_", name).strip("_")
        if not name:
            return None
        desc = input("  Description (what does it do?): ").strip()
        return name, desc or f"Custom action: {name}"
    except (KeyboardInterrupt, EOFError):
        return None


def print_segment_result(info: dict):
    print(f"  ✅ {info['filename']} — "
          f"{info['byte_count']} bytes, "
          f"{info['frame_count']} frames "
          f"({info['broadcast_count']} bc, "
          f"{info['command_candidate_count']} cmd), "
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
# Action runner
# ──────────────────────────────────────────────────────────────

def run_action(
    session: CaptureSession,
    action_name: str,
    action_desc: str,
    instruction: str,
    has_observe: bool,
    completed_map: dict[tuple[str, str], str],
    action_idx: int,
    total_actions: int,
) -> bool:
    """Run capture for one action. Returns False if interrupted."""

    print(f"\n{'━' * 70}")
    print(f"  [{action_idx}/{total_actions}] {action_name}")
    print(f"  {action_desc}")
    print(f"{'━' * 70}")

    # Check availability
    answer = prompt_yes_no("  Available on your panel?")
    if answer is None:
        session.mark_interrupted()
        return False
    if not answer:
        print(f"  ⏭  Skipping {action_name}")
        return True

    # --- Phase 1: Baseline ---
    if (action_name, "baseline") in completed_map:
        print(f"\n  ⏭  baseline already captured")
    else:
        print(f"\n  ── BASELINE (10s, don't touch anything) ──")
        if not prompt_continue("  Press Enter to start baseline capture..."):
            session.mark_interrupted()
            return False

        print(f"  ⏺  Capturing baseline (10s)...", end="", flush=True)
        try:
            info = session.capture_baseline(action_name)
        except (OSError, socket.error) as err:
            print(f"\n  ❌ Connection error: {err}")
            session.mark_interrupted()
            return False
        print(" done!")
        print_segment_result(info)
        completed_map[(action_name, "baseline")] = info["filename"]

    baseline_state = None
    # Get baseline state from last baseline segment for this action
    for seg in reversed(session.segments):
        if seg.get("action") == action_name and seg.get("phase") == "baseline":
            baseline_state = seg.get("broadcast_state")
            break

    # --- Phase 2: Action (interactive) ---
    if (action_name, "press") in completed_map:
        print(f"\n  ⏭  press already captured")
    else:
        print(f"\n  ── ACTION ──")
        print(f"  📋 {instruction}")
        print()
        print("     Recording starts when you press Enter.")
        print("     Perform the action, then press Enter again to stop.")
        if not prompt_continue("  Press Enter to START recording..."):
            session.mark_interrupted()
            return False

        try:
            info = session.capture_action(action_name)
        except (OSError, socket.error) as err:
            print(f"\n  ❌ Connection error: {err}")
            session.mark_interrupted()
            return False
        print_segment_result(info)
        completed_map[(action_name, "press")] = info["filename"]

        # Show diff vs baseline
        press_state = info.get("broadcast_state")
        if baseline_state and press_state:
            changes = diff_broadcast_states(baseline_state, press_state)
            if any("→" in c for c in changes):
                print(f"     📋 Changes vs baseline:")
                for c in changes:
                    print(f"     {c}")

    # --- Phase 3: Observe (optional) ---
    if has_observe:
        if (action_name, "observe") in completed_map:
            print(f"\n  ⏭  observe already captured")
        else:
            print(f"\n  ── OBSERVE (10s, don't touch anything) ──")
            if not prompt_continue("  Press Enter to capture post-action state..."):
                session.mark_interrupted()
                return False

            print(f"  ⏺  Observing new state (10s)...", end="", flush=True)
            try:
                info = session.capture_observe(action_name)
            except (OSError, socket.error) as err:
                print(f"\n  ❌ Connection error: {err}")
                session.mark_interrupted()
                return False
            print(" done!")
            print_segment_result(info)
            completed_map[(action_name, "observe")] = info["filename"]

            # Diff baseline vs observe
            observe_state = info.get("broadcast_state")
            if baseline_state and observe_state:
                changes = diff_broadcast_states(baseline_state, observe_state)
                print(f"     📋 State changes (baseline → after):")
                for c in changes:
                    print(f"     {c}")

    notes = prompt_notes(action_name)
    if notes:
        # Append notes to last segment
        if session.segments:
            session.segments[-1]["notes"] = notes
            session.save_manifest()

    return True


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Phase 6 — Full functionality capture for Joyonway P25B85",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Examples:
  %(prog)s                                         # Full guided session
  %(prog)s --actions light_toggle_on,blower_on     # Specific actions only
  %(prog)s --skip-predefined                       # Jump straight to ad-hoc
  %(prog)s --dry-run                               # Simulate without bridge
  %(prog)s --fresh                                 # Ignore previous session

Predefined actions:
  {chr(10).join(f'  {a[0]:30s} {a[1]}' for a in PREDEFINED_ACTIONS)}
""",
    )
    parser.add_argument(
        "--host", default=_DEFAULT_HOST,
        help=f"RS485 bridge IP (default: from .env or {_DEFAULT_HOST})",
    )
    parser.add_argument(
        "--port", type=int, default=_DEFAULT_PORT,
        help=f"RS485 bridge TCP port (default: {_DEFAULT_PORT})",
    )
    parser.add_argument(
        "--actions",
        help="Comma-separated list of specific actions to capture.",
    )
    parser.add_argument(
        "--skip-predefined", action="store_true",
        help="Skip all predefined actions and go straight to ad-hoc mode.",
    )
    _default_out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures_phase6")
    parser.add_argument(
        "--out-dir", default=_default_out,
        help=f"Output directory (default: {_default_out})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Simulate capture without connecting to bridge",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Start fresh session (existing .bin files kept, new indices)",
    )
    return parser.parse_args()


def select_actions(args: argparse.Namespace) -> list[tuple[str, str, str, bool]]:
    """Returns list of (name, description, instruction, has_observe)."""
    if args.skip_predefined:
        return []

    if args.actions:
        action_map = {a[0]: a for a in PREDEFINED_ACTIONS}
        selected = []
        for name in args.actions.split(","):
            name = name.strip().lower()
            if name in action_map:
                selected.append(action_map[name])
            else:
                print(f"⚠️  Unknown action '{name}', skipping.")
                print(f"    Available: {', '.join(ALL_ACTION_NAMES)}")
        return selected

    return list(PREDEFINED_ACTIONS)


def main():
    args = parse_args()
    actions = select_actions(args)

    print_banner()
    print_bridge_info(args.host, args.port)

    if args.dry_run:
        print("🧪 DRY-RUN MODE — no real TCP connection\n")

    print(f"Actions:  {len(actions)} predefined + unlimited ad-hoc")
    print(f"Output:   {os.path.abspath(args.out_dir)}")
    print()

    # Resume handling
    existing_manifest = load_manifest(args.out_dir) if not args.fresh else None
    existing_segments: list[dict] = []
    existing_started_at: str | None = None
    resume_mode = False

    if existing_manifest:
        segs = existing_manifest.get("segments", [])
        if isinstance(segs, list):
            existing_segments = [s for s in segs if isinstance(s, dict)]
        session_meta = existing_manifest.get("session", {})
        if isinstance(session_meta, dict):
            sa = session_meta.get("started_at")
            if isinstance(sa, str):
                existing_started_at = sa

    completed_map = build_completed_map(existing_segments)

    # Also scan bin files on disk
    if os.path.isdir(args.out_dir):
        for name in os.listdir(args.out_dir):
            m = re.match(r"^\d+_(.+?)_(baseline|press|observe)\.bin$", name)
            if m:
                action, phase = m.group(1), m.group(2)
                if (action, phase) not in completed_map:
                    completed_map[(action, phase)] = name

    if existing_segments:
        resume_mode = True
        print(f"📂 Resuming session with {len(existing_segments)} existing segment(s).")
        print()

    if not prompt_continue("Press Enter to begin (Ctrl-C to abort)... "):
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
        print("\n\n⚠️  Interrupted! Manifest already saved (auto-save after each segment).")
        manifest_path = os.path.join(session.out_dir, "session_manifest.json")
        print(f"   {manifest_path}")
        sys.exit(130)

    signal.signal(signal.SIGINT, signal_handler)

    # --- Run predefined actions ---
    total = len(actions)
    for idx, (name, desc, instruction, has_observe) in enumerate(actions, 1):
        # Check if ALL phases already done
        phases_needed = ["baseline", "press"] + (["observe"] if has_observe else [])
        if all((name, p) in completed_map for p in phases_needed):
            print(f"\n  ⏭  {name} — all phases already captured")
            continue

        ok = run_action(
            session, name, desc, instruction, has_observe,
            completed_map, idx, total,
        )
        if not ok:
            break

    # --- Ad-hoc capture loop ---
    if not session.interrupted:
        print(f"\n{'━' * 70}")
        print("  🔬 AD-HOC CAPTURE MODE")
        print("  Capture any additional functions you discover at the spa.")
        print("  Type 'done' or press Ctrl-C to finish the session.")
        print(f"{'━' * 70}")

        adhoc_idx = 0
        while not session.interrupted:
            print()
            answer = prompt_yes_no("  Capture another action?")
            if answer is None or not answer:
                break

            result = prompt_custom_action()
            if result is None:
                break
            name, desc = result
            adhoc_idx += 1

            instruction = input("  What should you do on the panel? ").strip()
            if not instruction:
                instruction = desc

            has_obs = prompt_yes_no("  Capture observe phase after action?")
            if has_obs is None:
                session.mark_interrupted()
                break

            ok = run_action(
                session, name, desc, instruction, bool(has_obs),
                completed_map, adhoc_idx, -1,  # -1 = unknown total
            )
            if not ok:
                break

    # --- Summary ---
    print(f"\n{'━' * 70}")
    manifest_path = session.save_manifest()
    if session.interrupted:
        print(f"\n⚠️  Session ended early. {len(session.segments)} segments captured.")
    else:
        print(f"\n✅ Session complete! {len(session.segments)} segments captured.")
    print(f"   Manifest: {manifest_path}")
    print(f"   Output:   {os.path.abspath(args.out_dir)}")
    print()

    # State summary
    print("📊 Captured states summary:")
    print("─" * 70)
    for seg in session.segments:
        state = seg.get("broadcast_state")
        if state:
            print(f"  {seg['action']}/{seg['phase']}: "
                  f"heater={state.get('heater_byte','?')} "
                  f"pump={state.get('pump_byte','?')} "
                  f"light={state.get('light_byte','?')} "
                  f"activity={state.get('activity_byte','?')}")
    print()

    # Command frames found
    cmd_segments = [s for s in session.segments
                    if s.get("phase") == "press" and s.get("command_candidate_count", 0) > 0]
    if cmd_segments:
        print(f"🔍 Segments with command candidates: {len(cmd_segments)}")
        for s in cmd_segments:
            print(f"   {s['filename']} — {s['command_candidate_count']} candidate(s)")
        print()

    print("Next steps:")
    print(f"  1. Analyze captures:  python3 tools/decode_phase4.py {args.out_dir}")
    print(f"  2. Compare baselines vs press segments to isolate command bytes")
    print(f"  3. Verify CRC on found command frames")
    print()


if __name__ == "__main__":
    main()

