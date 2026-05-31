#!/usr/bin/env python3
"""
Phase 4b — Temperature command frame capture (all setpoints).

Captures one command frame per 1 degree C by having you press the UP or
DOWN button once per capture window. Fully automated timing — you only
press Enter once to start, then follow the audio/visual prompts.

Modes:
  Full capture:  Set spa to 10 deg C, run with --steps 30
  Fill gaps:     Set spa one step below a gap, run with --steps N

The command frame encodes the target temperature (byte[15] in deg F).
One direction is sufficient. Duplicates are silently skipped.

Abort with Ctrl-C at any time — progress is saved.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time

__version__ = "2.1.0"

# Load .env
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

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

# Pseudo-escape table: escaped pair suffix -> original byte
ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}


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


def unescape_frame(frame: bytes) -> bytes:
    """Unescape everything between start and end delimiters (P25B85 policy)."""
    return frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]

# Temperature range: 10-40 deg C in 1 deg C steps = 31 values, 30 button presses
TEMP_MIN_C = 10
TEMP_MAX_C = 40
TEMP_STEPS = TEMP_MAX_C - TEMP_MIN_C  # 30 presses


def extract_command_frames(data: bytes, baseline_set: set[str]) -> list[bytes]:
    """Extract non-broadcast frames that aren't in the baseline set."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frame = data[i:j+1]
                    if len(frame) > 1 and frame[1] != 0xFF:
                        hex_str = frame.hex()
                        if hex_str not in baseline_set:
                            frames.append(frame)
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def extract_all_frames(data: bytes) -> list[bytes]:
    """Extract all frames from raw data."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frames.append(data[i:j+1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def capture_raw(host: str, port: int, duration: float) -> bytes:
    """Capture raw bytes from TCP bridge for given duration."""
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


def fahrenheit_to_celsius(f: int) -> float:
    return round((f - 32) * 5 / 9, 1)


# Expected deg F sequence for 10-40 deg C (pattern: +2,+2,+2,+2,+1 repeating from 50)
def _expected_sequence() -> list[int]:
    """Return all expected deg F values for 10-40 deg C (31 values including 50)."""
    seq = [50]  # 10 deg C
    diffs = [1, 2, 2, 2, 2]  # repeating 5-step pattern (sum=9 per 5 deg C)
    for i in range(30):
        seq.append(seq[-1] + diffs[i % 5])
    return seq


def main():
    parser = argparse.ArgumentParser(
        description="Capture temperature command frames (10-40 deg C, 1 deg C steps)",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--duration", type=float, default=15.0,
                        help="Capture duration per step (default: 15s)")
    parser.add_argument("--steps", type=int, default=TEMP_STEPS,
                        help=f"Number of button presses (default: {TEMP_STEPS})")
    parser.add_argument("--button", choices=["up", "down"], default="up",
                        help="Which button to press (default: up)")
    _default_out = os.path.join(os.path.dirname(__file__), "captures_temp")
    parser.add_argument("--out-dir", default=_default_out,
                        help="Output directory (default: tools/captures_temp)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    button_label = args.button.upper()

    os.makedirs(args.out_dir, exist_ok=True)
    results_file = os.path.join(args.out_dir, "temp_commands.json")

    # Load existing results
    existing: dict[str, str] = {}
    if os.path.exists(results_file):
        with open(results_file) as f:
            existing = json.load(f)
        # Remove old tracking key if present
        existing.pop("_next_press", None)

    # Analyze coverage
    captured_temps = sorted(
        int(k.rstrip("F")) for k in existing if k.endswith("F")
    )
    expected = _expected_sequence()
    missing = [t for t in expected if t not in captured_temps]

    print()
    print("=" * 66)
    print("  Joyonway P25B85 — Temperature Command Capture")
    print("=" * 66)
    print()
    print(f"  Captured: {len(captured_temps)}/{len(expected)} temperatures")
    if missing:
        print(f"  Missing:  {len(missing)} values:")
        for t in missing:
            print(f"            {t} deg F = {fahrenheit_to_celsius(t)} deg C")
    else:
        print("  All temperatures captured!")
        sys.exit(0)
    print()
    print(f"  This session: {args.steps} presses ({button_label})")
    print(f"  Duration: {args.duration:.0f}s per window")
    print()
    print("HOW IT WORKS:")
    print("  1. Press Enter ONCE to start")
    print(f"  2. When prompted, press {button_label} ONCE on the panel")
    print("  3. Already-captured temperatures are silently skipped")
    print("  4. Ctrl-C to abort (progress is saved)")
    print()
    print(f"  Bridge: {args.host}:{args.port}")
    print(f"  Output: {os.path.abspath(args.out_dir)}")
    print()

    if args.dry_run:
        print("[DRY-RUN MODE]\n")

    try:
        input("Press Enter to start the sequence... ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    # Capture baseline
    print("\n  Capturing baseline (don't press anything)...", end="", flush=True)
    if args.dry_run:
        baseline_data = b""
    else:
        baseline_data = capture_raw(args.host, args.port, 5.0)
    baseline_frames = set(f.hex() for f in extract_all_frames(baseline_data)
                         if len(f) > 1 and f[1] != 0xFF)
    print(f" done ({len(baseline_frames)} baseline frame types)")

    # Ctrl-C handler
    interrupted = False
    def signal_handler(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n\n  Interrupted! Saving progress...")

    signal.signal(signal.SIGINT, signal_handler)

    captured_count = 0
    skipped_count = 0

    for press_num in range(1, args.steps + 1):
        if interrupted:
            break

        print(f"\n{'=' * 66}")
        print(f"  Press #{press_num}/{args.steps}")
        print(f"  Press {button_label} once when prompted")
        print(f"{'=' * 66}")

        # Countdown
        print(f"  Get ready... capturing in: ", end="", flush=True)
        for i in range(3, 0, -1):
            if interrupted:
                break
            print(f"{i}...", end="", flush=True)
            time.sleep(1)
        print()

        if interrupted:
            break

        # Start capture
        print(f"  CAPTURING ({args.duration:.0f}s) — ", end="", flush=True)
        time.sleep(2)
        print(f">>> PRESS {button_label} NOW! <<<", end="", flush=True)

        if args.dry_run:
            time.sleep(args.duration - 2)
            raw_data = b""
            new_frames = []
        else:
            raw_data = capture_raw(args.host, args.port, args.duration - 2)
            new_frames = extract_command_frames(raw_data, baseline_frames)

        print(" done!")

        # Look for command frames: unescape first, then check for 22-byte / dest 0x01
        cmd_frames = []
        for f in new_frames:
            uf = unescape_frame(f)
            if len(uf) == 22 and uf[1] == 0x01:
                cmd_frames.append((f, uf))  # (raw_wire, unescaped)

        if cmd_frames:
            raw_frame, frame = cmd_frames[0]
            # Store the RAW wire frame (with escapes) — that's what we replay
            hex_str = raw_frame.hex()
            setpoint_f = frame[15]
            setpoint_c = fahrenheit_to_celsius(setpoint_f)
            key = f"{setpoint_f}F"

            escaped = len(raw_frame) != len(frame)
            esc_note = f" (wire: {len(raw_frame)}B, escaped)" if escaped else ""

            if key in existing:
                print(f"  SKIP: {setpoint_f} deg F ({setpoint_c} deg C) already captured")
                skipped_count += 1
            else:
                print(f"  NEW:  {setpoint_f} deg F ({setpoint_c} deg C){esc_note}")
                print(f"        Frame: {hex_str}")
                existing[key] = hex_str
                captured_count += 1

        elif new_frames:
            print(f"  Found {len(new_frames)} non-broadcast frames but none match "
                  f"22-byte command format:")
            for nf in new_frames[:3]:
                print(f"     [{len(nf)}B] {nf.hex()}")
        else:
            print(f"  No command frame detected! (Did you press the button?)")
            print(f"     Raw: {len(raw_data)} bytes captured")

        # Save progress after each step
        with open(results_file, "w") as f:
            json.dump(existing, f, indent=2)

    # Final summary
    captured_temps = sorted(
        int(k.rstrip("F")) for k in existing if k.endswith("F")
    )
    expected = _expected_sequence()
    still_missing = [t for t in expected if t not in captured_temps]

    print(f"\n\n{'=' * 66}")
    print(f"  Session: {captured_count} new, {skipped_count} skipped")
    print(f"  Total:   {len(captured_temps)}/{len(expected)} temperatures")
    if still_missing:
        print(f"  Still missing: {', '.join(f'{t}F ({fahrenheit_to_celsius(t)}C)' for t in still_missing)}")
    else:
        print("  COMPLETE! All temperatures captured.")
    print(f"  Saved to: {results_file}")
    print(f"{'=' * 66}")
    print()



if __name__ == "__main__":
    main()

