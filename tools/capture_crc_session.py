#!/usr/bin/env python3
"""
CRC cracking capture — ALL command types in ONE session.

The CRC algorithm appears session-dependent: frames captured in different
sessions have incompatible CRC values (GF(2) GCD gives 1 = coprime).

To crack the polynomial we need many command frames from a SINGLE session
where different byte positions change. This script captures them all in
rapid succession without disconnecting, giving the GF(2) GCD algorithm
the best chance.

What we need (per the CRC analysis in docs/crc_analysis.md):
  1. Commands where only byte[15] differs (temp setpoint changes)
     → We already proved linearity with these, but need them in-session
  2. Commands where byte[8-9] differ (pump transitions)
     → Need per-byte CRC contributions for pump bytes
  3. Commands where byte[10-11] differ (light toggle, heater on/off)
     → Need per-byte CRC contributions for button flag bytes
  4. Temp commands crossing byte[11] groups (0x88 → 0x98 boundary)
     → Need byte[11] CRC contribution to predict across groups

Ideal sequence (all in ONE uninterrupted session):
  1. Temp up ×4  (byte[15] varies, gives 4 frames within a byte[11] group)
  2. Temp down ×4 (byte[15] varies back, 4 more frames)
  3. Light toggle ON  (byte[10-11] = 0x40,0x40)
  4. Light toggle OFF (same frame)
  5. Pump OFF→low   (byte[8-9] = 0x02,0x02)
  6. Pump low→high  (byte[8-9] = 0x06,0x04)
  7. Pump high→OFF  (byte[8-9] = 0x04,0x00)
  8. Heater ON      (byte[10-11] = 0x08,0x08)
  9. Heater OFF     (byte[10-11] = 0x08,0x00)
  10. Blower ON     (unknown byte changes — bonus data)
  11. Blower OFF    (unknown byte changes — bonus data)
  12. Temp up ×2 more (cross byte[11] boundary if near 25°C/77°F)

The more different byte positions we see change, the more constraints
we have on the polynomial. 15+ frames should be enough.

Python stdlib only — no pip dependencies required.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import signal
import socket
import sys
import time

__version__ = "1.0.0-crc"

# Load .env if present
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
        return 8899

_DEFAULT_PORT = _get_default_port()
_DEFAULT_OUT_DIR = os.path.join(os.path.dirname(__file__), "captures_crc")

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B
ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

# ──────────────────────────────────────────────────────────────
# CRC session actions — designed for maximum byte-position coverage
# ──────────────────────────────────────────────────────────────

CORE_CRC_ACTIONS = [
    # Temperature: 4x up → byte[15] changes, staying in same byte[11] group
    ("crc_temp_up_1",
     "TEMP UP ×1 — press temp UP once on PB554.\n"
     "     This changes byte[15] by +1 or +2 °F.\n"
     "     Starting point for CRC extraction."),

    ("crc_temp_up_2",
     "TEMP UP ×2 — press temp UP once more.\n"
     "     Another byte[15] change. Same byte[11] group."),

    ("crc_temp_up_3",
     "TEMP UP ×3 — press temp UP once more.\n"
     "     Third byte[15] data point."),

    ("crc_temp_up_4",
     "TEMP UP ×4 — press temp UP once more.\n"
     "     Fourth byte[15] data point. May cross byte[11] boundary."),

    # Temperature: 4x down → byte[15] changes back
    ("crc_temp_down_1",
     "TEMP DOWN ×1 — press temp DOWN once.\n"
     "     Reverses the last change. More byte[15] data."),

    ("crc_temp_down_2",
     "TEMP DOWN ×2 — press temp DOWN once more."),

    ("crc_temp_down_3",
     "TEMP DOWN ×3 — press temp DOWN once more."),

    ("crc_temp_down_4",
     "TEMP DOWN ×4 — press temp DOWN once more.\n"
     "     Should be back at original setpoint now."),

    # Light: byte[10-11] = 0x40,0x40
    ("crc_light_on",
     "LIGHT ON — press Light button ON on PB554.\n"
     "     If light is already on, skip this and go to light off.\n"
     "     byte[10-11] = 0x40,0x40 — different from temp/pump."),

    ("crc_light_off",
     "LIGHT OFF — press Light button OFF on PB554.\n"
     "     Same toggle frame as ON — but captured separately to confirm."),

    # Pump cycle: byte[8-9] vary
    ("crc_pump_off_to_low",
     "PUMP: OFF → LOW — press pump button once (OFF→low).\n"
     "     byte[8-9] = 0x02,0x02. Different byte positions from temp/light."),

    ("crc_pump_low_to_high",
     "PUMP: LOW → HIGH — press pump button again (low→high).\n"
     "     byte[8-9] = 0x06,0x04. Another byte[8-9] combination."),

    ("crc_pump_high_to_off",
     "PUMP: HIGH → OFF — press pump button again (high→OFF).\n"
     "     byte[8-9] = 0x04,0x00. Third byte[8-9] combination."),

    # Heater: byte[10-11] = 0x08,0x08/0x08,0x00
    ("crc_heater_on",
     "HEATER ON — navigate menu and START heating.\n"
     "     byte[10-11] = 0x08,0x08. Same byte positions as light but different values."),

    ("crc_heater_off",
     "HEATER OFF — navigate menu and STOP heating.\n"
     "     byte[10-11] = 0x08,0x00. Note: byte[11] differs from heater ON."),

    # Blower (unknown byte pattern — fresh data for CRC analysis)
    ("crc_blower_on",
     "BLOWER ON — press Blower button on PB554.\n"
     "     Unknown byte pattern — this gives us NEW byte positions for CRC."),

    ("crc_blower_off",
     "BLOWER OFF — press Blower button again.\n"
     "     Second blower frame for comparison."),

    # Extra temperature changes to cross byte[11] boundary
    # Byte[11] = 0x88 below ~77°F, 0x98 at ~77-78°F, 0x99 above ~89°F
    ("crc_temp_up_5",
     "TEMP UP ×5 — press temp UP once more.\n"
     "     Extra temp frame. If near 25°C/77°F, this may cross the byte[11]\n"
     "     boundary (0x88→0x98), giving us byte[11] CRC contribution."),

    ("crc_temp_up_6",
     "TEMP UP ×6 — press temp UP once more.\n"
     "     Another chance to cross the byte[11] boundary."),

    ("crc_temp_down_5",
     "TEMP DOWN ×5 — press temp DOWN once.\n"
     "     Cross back over the boundary if we went past it."),

    ("crc_temp_down_6",
     "TEMP DOWN ×6 — press temp DOWN once.\n"
     "     Back to original temp. Should now have 12+ temp frames total."),
]


CONFIG_CRC_ACTIONS = [
    ("crc_datetime_set",
     "DATE/TIME SET — save/set the spa clock on PB554.\n"
     "     Optional CRC data: command type byte[4] = 0xA2.\n"
     "     This is usually safe, but it changes the controller clock."),

    ("crc_filter_schedule",
     "FILTER SCHEDULE — save a filtration schedule/time window.\n"
     "     Optional CRC data: command type byte[4] = 0xA4.\n"
     "     Only do this if you are ready to preserve or restore the schedule."),

    ("crc_heat_schedule",
     "HEAT SCHEDULE — save a timed heating schedule/window.\n"
     "     Optional CRC data: command type byte[4] = 0xA3.\n"
     "     Only do this if you are ready to preserve or restore the schedule."),
]


# ──────────────────────────────────────────────────────────────
# Frame helpers
# ──────────────────────────────────────────────────────────────

def pseudo_unescape(data: bytes) -> bytes:
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == ESCAPE_BYTE and i + 1 < len(data) and data[i + 1] in ESCAPE_MAP:
            result.append(ESCAPE_MAP[data[i + 1]])
            i += 2
        else:
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
                    frames.append(data[i:j + 1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def extract_panel_cmds(data: bytes) -> list[bytes]:
    """Extract panel→controller commands (22-byte frames starting 1A 01 20)."""
    cmds = []
    for frame in find_frames(data):
        if len(frame) > 2 and frame[1] == 0x01 and frame[2] == 0x20:
            cmds.append(frame)
    return cmds


def extract_broadcast_state(data: bytes) -> dict | None:
    for frame in find_frames(data):
        if len(frame) > 1 and frame[1] == 0xFF:
            unesc = frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
            if len(unesc) >= 30:
                return {
                    "water_temp_f": unesc[9],
                    "pump_byte": f"0x{unesc[12]:02X}",
                    "heater_byte": f"0x{unesc[14]:02X}",
                    "setpoint_f": unesc[16],
                    "light_byte": f"0x{unesc[17]:02X}",
                }
    return None


def load_resume(out_dir: str) -> tuple[list[dict], dict[str, list[str]], set[str]]:
    """Load previous capture metadata and return completed action names."""
    manifest_path = os.path.join(out_dir, "crc_session.json")
    if not os.path.isfile(manifest_path):
        return [], {}, set()

    with open(manifest_path) as f:
        data = json.load(f)

    segments = []
    all_commands: dict[str, list[str]] = {}
    completed = set()

    for segment in data.get("segments", []):
        action = segment.get("action")
        baseline_file = segment.get("baseline_file")
        press_file = segment.get("press_file")
        if not action or not baseline_file or not press_file:
            continue
        if not os.path.isfile(os.path.join(out_dir, baseline_file)):
            continue
        if not os.path.isfile(os.path.join(out_dir, press_file)):
            continue
        segments.append(segment)
        completed.add(action)
        all_commands[action] = list(segment.get("new_commands", []))

    return segments, all_commands, completed


# ──────────────────────────────────────────────────────────────
# Capture
# ──────────────────────────────────────────────────────────────

def capture_segment(host: str, port: int, duration: float) -> bytes:
    sock = socket.create_connection((host, port), timeout=10.0)
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


class PersistentCapture:
    """Keep one TCP connection open for the whole guided CRC capture."""

    def __init__(self, host: str, port: int) -> None:
        self._sock = socket.create_connection((host, port), timeout=10.0)
        self._sock.settimeout(1.0)

    def close(self) -> None:
        self._sock.close()

    def drain(self) -> int:
        """Discard bytes received while the user was reading prompts."""
        drained = 0
        self._sock.setblocking(False)
        try:
            while True:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("TCP bridge closed the connection")
                drained += len(chunk)
        except BlockingIOError:
            return drained
        finally:
            self._sock.setblocking(True)
            self._sock.settimeout(1.0)

    def capture(self, duration: float) -> bytes:
        self.drain()
        buf = bytearray()
        deadline = time.time() + duration
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(4096)
                if not chunk:
                    raise ConnectionError("TCP bridge closed the connection")
                buf.extend(chunk)
            except socket.timeout:
                continue
        return bytes(buf)


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CRC cracking capture — all command types in one session",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
This script captures ALL command types in a SINGLE session to crack the CRC.

The CRC appears session-dependent: frames from different sessions have
incompatible CRC values. By capturing pump, light, temp, heater, and blower
commands all in one go, we eliminate session state as a variable.

The result is saved as a JSON file that can be fed directly into crack_crc.py.

Tips:
  - Run with no arguments for the recommended full capture
  - You can stop any time; the next run resumes from completed actions
  - Press the button ~3 seconds into each capture window
  - The script uses short 10-second windows to keep it fast
""",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--duration", type=float, default=10.0,
                        help="Capture duration per segment (default: 10s)")
    parser.add_argument("--out-dir", default=_DEFAULT_OUT_DIR,
                        help="Output directory (default: tools/captures_crc)")
    parser.add_argument("--core-only", action="store_true",
                        help="Skip date/time and schedule commands")
    parser.add_argument("--fresh", action="store_true",
                        help="Start from the first action instead of resuming")
    parser.add_argument("--no-persistent", action="store_true",
                        help="Open a new TCP connection for each capture window")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    actions = list(CORE_CRC_ACTIONS)
    if not args.core_only:
        actions.extend(CONFIG_CRC_ACTIONS)
    selected_action_names = {name for name, _desc in actions}

    out_dir = args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    resumed_segments: list[dict] = []
    resumed_commands: dict[str, list[str]] = {}
    completed_actions: set[str] = set()
    if not args.fresh:
        resumed_segments, resumed_commands, completed_actions = load_resume(out_dir)
        resumed_segments = [seg for seg in resumed_segments if seg.get("action") in selected_action_names]
        resumed_commands = {name: cmds for name, cmds in resumed_commands.items() if name in selected_action_names}
        completed_actions = {name for name in completed_actions if name in selected_action_names}

    actions_to_capture = [(name, desc) for name, desc in actions if name not in completed_actions]

    print()
    print("=" * 70)
    print("  CRC Cracking Capture — ALL command types in ONE session")
    print(f"  Version {__version__}")
    print("=" * 70)
    print()
    print("WHY: The CRC includes session-dependent state. Frames from different")
    print("capture sessions are incompatible for polynomial extraction.")
    print()
    print("GOAL: Capture pump, light, temp, heater, and blower commands all in")
    print("one uninterrupted session so the GF(2) GCD algorithm can extract the")
    print("CRC polynomial.")
    print()
    print("RULES:")
    print("  • Do NOT disconnect the bridge between actions")
    print("  • Stop Home Assistant integration BEFORE starting")
    print("  • Press the button ~3 seconds into each capture window")
    print("  • Be quick — we use short 10-second capture windows")
    print()
    print(f"Bridge:    {args.host}:{args.port}")
    estimated_seconds = len(actions_to_capture) * (args.duration * 2 + 8)
    print(f"Duration:  {args.duration}s per segment ({len(actions)} total actions, baseline + press each)")
    print(f"Total:    ~{estimated_seconds:.0f}s ({estimated_seconds / 60:.1f} min)")
    print(f"Output:    {os.path.abspath(out_dir)}")
    print(f"TCP mode:  {'new connection per window' if args.no_persistent else 'persistent single connection'}")
    print(f"Config:    {'skipped' if args.core_only else 'included'}")
    print(f"Resume:    {'off (--fresh)' if args.fresh else f'{len(completed_actions)} completed, {len(actions_to_capture)} remaining'}")
    print()

    if not actions_to_capture:
        print("All requested actions are already captured. Use --fresh to start over.")
        print(f"Next: python3 tools/crack_crc.py --input {os.path.join(out_dir, 'crc_session.json')}")
        return

    try:
        input("Press Enter to begin (Ctrl-C to abort)... ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    session_start = datetime.datetime.now(datetime.timezone.utc)
    segments = list(resumed_segments)
    all_commands: dict[str, list[str]] = dict(resumed_commands)  # action → list of hex command frames
    interrupted = False

    def on_interrupt(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n\n⚠️  Interrupted! Saving what we have...")

    signal.signal(signal.SIGINT, on_interrupt)

    capture: PersistentCapture | None = None
    try:
        if not args.dry_run and not args.no_persistent:
            print("Opening persistent TCP capture connection...", end="", flush=True)
            capture = PersistentCapture(args.host, args.port)
            print(" connected")

        for action_index, (action_name, action_desc) in enumerate(actions, start=1):
            if interrupted:
                break
            if action_name in completed_actions:
                print(f"  [{action_index}/{len(actions)}] {action_name} — already captured, skipping")
                continue

            print(f"\n{'━' * 70}")
            print(f"  [{action_index}/{len(actions)}] {action_name}")
            print(f"  {action_desc}")
            print(f"{'━' * 70}")

            # Baseline
            try:
                input(f"\n  Press Enter to capture BASELINE ({args.duration}s, don't touch anything)... ")
            except (KeyboardInterrupt, EOFError):
                interrupted = True
                break

            print(f"  ⏺  Capturing baseline...", end="", flush=True)
            counter = (action_index - 1) * 2
            baseline_file = f"{counter:02d}_{action_name}_baseline.bin"
            if args.dry_run:
                baseline_data = b"\x1a\x10\x01\x03\x00\x1d" * 50
            else:
                try:
                    baseline_data = capture.capture(args.duration) if capture else capture_segment(args.host, args.port, args.duration)
                except (OSError, socket.error, ConnectionError) as err:
                    print(f"\n  ❌ Connection error: {err}")
                    interrupted = True
                    break
            with open(os.path.join(out_dir, baseline_file), "wb") as f:
                f.write(baseline_data)
            baseline_cmds = set(cmd.hex() for cmd in extract_panel_cmds(baseline_data))
            state = extract_broadcast_state(baseline_data)
            state_str = ""
            if state:
                state_str = (f" [temp={state['water_temp_f']}°F set={state['setpoint_f']}°F "
                             f"pump={state['pump_byte']} heat={state['heater_byte']} "
                             f"light={state['light_byte']}]")
            print(f" done ({len(baseline_data)} bytes){state_str}")

            # Press
            print()
            print("     ┌──────────────────────────────────────────────────────┐")
            print("     │  ⚡ After Enter: wait ~3s, then PRESS THE BUTTON    │")
            print("     └──────────────────────────────────────────────────────┘")
            try:
                input(f"  Press Enter to capture PRESS ({args.duration}s)... ")
            except (KeyboardInterrupt, EOFError):
                interrupted = True
                break

            print(f"  ⏺  Capturing press...", end="", flush=True)
            press_file = f"{counter + 1:02d}_{action_name}_press.bin"
            if args.dry_run:
                press_data = b"\x1a\x10\x01\x03\x00\x1d" * 50
            else:
                try:
                    press_data = capture.capture(args.duration) if capture else capture_segment(args.host, args.port, args.duration)
                except (OSError, socket.error, ConnectionError) as err:
                    print(f"\n  ❌ Connection error: {err}")
                    interrupted = True
                    break
            with open(os.path.join(out_dir, press_file), "wb") as f:
                f.write(press_data)
            print(f" done ({len(press_data)} bytes)")

            # Extract NEW commands (in press but not in baseline)
            press_cmds = extract_panel_cmds(press_data)
            new_cmds = [cmd for cmd in press_cmds if cmd.hex() not in baseline_cmds]

            if new_cmds:
                # Deduplicate
                unique_new = {}
                for cmd in new_cmds:
                    h = cmd.hex()
                    unique_new[h] = unique_new.get(h, 0) + 1

                print(f"\n  ⚡ Found {len(unique_new)} NEW command frame(s):")
                all_commands[action_name] = []
                for hexstr, count in sorted(unique_new.items(), key=lambda x: -x[1]):
                    print(f"    [{count}x] {hexstr}")
                    unesc = bytes.fromhex(hexstr)
                    inner = unesc[:1] + pseudo_unescape(unesc[1:-1]) + unesc[-1:]
                    if len(inner) >= 22:
                        print(f"         type=0x{inner[4]:02X}  b[8:17]={inner[8:17].hex(' ')}"
                              f"  CRC={inner[17]:02x}{inner[18]:02x}{inner[19]:02x}{inner[20]:02x}")
                    all_commands[action_name].append(hexstr)
            else:
                print(f"\n  ⚠️  No NEW commands found — button press may have been missed!")
                all_commands[action_name] = []

            segments.append({
                "action": action_name,
                "baseline_file": baseline_file,
                "press_file": press_file,
                "new_commands": [cmd.hex() for cmd in new_cmds],
                "broadcast_state": state,
            })
    finally:
        if capture:
            capture.close()

    # Save results
    session_end = datetime.datetime.now(datetime.timezone.utc)

    # Build the all-in-one JSON for crack_crc.py
    crc_frames = {}
    for action_name, cmds in all_commands.items():
        for i, hexstr in enumerate(cmds):
            key = action_name if i == 0 else f"{action_name}_{i}"
            crc_frames[key] = hexstr

    results = {
        "session": {
            "started_at": session_start.isoformat(),
            "ended_at": session_end.isoformat(),
            "host": args.host,
            "port": args.port,
            "tool_version": __version__,
            "dry_run": args.dry_run,
            "completed": not interrupted,
            "total_actions": len(actions),
            "captured_actions": len(segments),
            "unique_command_frames": len(crc_frames),
            "include_config": not args.core_only,
            "resumed": bool(completed_actions) and not args.fresh,
            "persistent_tcp": not args.no_persistent,
        },
        "frames": crc_frames,
        "segments": segments,
    }

    manifest_path = os.path.join(out_dir, "crc_session.json")
    with open(manifest_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'━' * 70}")
    if interrupted:
        print(f"\n⚠️  Session interrupted after {len(segments)} actions.")
    else:
        print(f"\n✅ CRC capture complete! {len(segments)} actions captured.")

    print(f"   Unique command frames: {len(crc_frames)}")
    print(f"   Session file: {manifest_path}")
    print()

    if crc_frames:
        print("📊 All captured command frames (for CRC analysis):")
        print("─" * 70)
        for name, hexstr in crc_frames.items():
            raw = bytes.fromhex(hexstr)
            inner = raw[:1] + pseudo_unescape(raw[1:-1]) + raw[-1:]
            if len(inner) >= 22:
                crc_le = int.from_bytes(inner[17:21], "little")
                print(f"  {name:30s} type=0x{inner[4]:02X} b[8:17]={inner[8:17].hex(' ')} CRC_LE={crc_le:08x}")
        print()

    print("Next steps:")
    print(f"  1. Run the CRC cracker on these same-session frames:")
    print(f"     python3 tools/crack_crc.py --input {manifest_path}")
    print()
    print(f"  2. If successful, we can compute CRC for ANY command frame")
    print(f"     and eliminate the replay-only limitation!")
    print()


if __name__ == "__main__":
    main()

