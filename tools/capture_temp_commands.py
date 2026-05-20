#!/usr/bin/env python3
"""
Phase 4b — Temperature command frame capture (all setpoints).

Captures one command frame per 1 degree C by having you press the UP
button once per capture window. Fully automated timing — you only press
Enter once to start, then follow the audio/visual prompts.

Strategy:
  1. Set spa to minimum (10 deg C / 50 deg F) manually before starting
  2. Run UP sequence: 30 presses (10 deg C -> 40 deg C in 1 deg C steps)

Each window: 15s capture. Press the button ~3s in. The script shows
the captured command frame immediately so you can verify it's working.

The command frame encodes the target temperature (byte[15] in deg F),
not the direction. One direction is sufficient to build the lookup table.

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

__version__ = "2.0.0"

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
    parser.add_argument("--out-dir", default="./captures_temp",
                        help="Output directory (default: ./captures_temp)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    results_file = os.path.join(args.out_dir, "temp_commands.json")

    # Load existing results if resuming
    existing: dict[str, str] = {}
    if os.path.exists(results_file):
        with open(results_file) as f:
            existing = json.load(f)
        print(f"Loaded {len(existing)} existing entries.")

    print()
    print("=" * 66)
    print("  Joyonway P25B85 — Temperature Command Capture")
    print("=" * 66)
    print()
    print("This captures one command frame per 1 deg C step.")
    print(f"  {args.steps} button presses (UP), one per capture window.")
    print()
    print("HOW IT WORKS:")
    print("  1. You press Enter ONCE to start")
    print(f"  2. Each step: {args.duration:.0f}s capture window")
    print("  3. When you see '>>> PRESS NOW <<<', press UP ONCE")
    print("  4. Wait for the result, then get ready for the next one")
    print("  5. Ctrl-C to abort (progress is saved)")
    print()
    print("PREPARATION:")
    print(f"  - Set spa to {TEMP_MIN_C} deg C before starting")
    print("  - Make sure no other client is connected to the EW11")
    print()
    print(f"  Bridge: {args.host}:{args.port}")
    print(f"  Output: {os.path.abspath(args.out_dir)}")
    print()

    if args.dry_run:
        print("[DRY-RUN MODE]\n")

    # Determine how many steps remain
    # We track by press number (1-based). The actual deg F value is read from byte[15].
    done_presses = existing.get("_next_press", 1) - 1
    remaining_start = done_presses + 1
    remaining_count = args.steps - done_presses

    if remaining_count <= 0:
        print(f"All {args.steps} presses already captured!")
        _print_summary(existing)
        sys.exit(0)

    print(f"Presses remaining: {remaining_count} (of {args.steps})")
    if done_presses > 0:
        print(f"  Resuming from press #{remaining_start}")
    print()

    try:
        input("Press Enter to start the sequence... ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    # Capture baseline (no button press) to filter normal bus traffic
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

    for press_num in range(remaining_start, args.steps + 1):
        if interrupted:
            break

        step_idx = press_num - remaining_start + 1
        total_remaining = remaining_count

        print(f"\n{'=' * 66}")
        print(f"  Press #{press_num}/{args.steps}  (step {step_idx}/{total_remaining})")
        print(f"  Press UP once when prompted")
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
        print(">>> PRESS UP NOW! <<<", end="", flush=True)

        if args.dry_run:
            time.sleep(args.duration - 2)
            raw_data = b""
            new_frames = []
        else:
            raw_data = capture_raw(args.host, args.port, args.duration - 2)
            new_frames = extract_command_frames(raw_data, baseline_frames)

        print(" done!")

        # Look for 22-byte command frames addressed to 0x01
        cmd_frames = [f for f in new_frames if len(f) == 22 and f[1] == 0x01]

        if cmd_frames:
            frame = cmd_frames[0]
            hex_str = frame.hex()
            setpoint_f = frame[15]
            setpoint_c = fahrenheit_to_celsius(setpoint_f)

            print(f"  OK! Frame captured")
            print(f"     Frame: {hex_str}")
            print(f"     Setpoint: {setpoint_f} deg F = {setpoint_c} deg C")

            # Store by actual deg F value from the frame
            existing[f"{setpoint_f}F"] = hex_str
            existing["_next_press"] = press_num + 1
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
    print(f"\n\n{'=' * 66}")
    print(f"  Session complete! Captured {captured_count} new frames.")
    _print_summary(existing)
    print(f"  Saved to: {results_file}")
    print(f"{'=' * 66}")
    print()


def _print_summary(data: dict[str, str]) -> None:
    """Print temperature coverage summary."""
    temps = sorted(
        int(k.rstrip("F"))
        for k in data
        if k.endswith("F") and not k.startswith("_")
    )
    if temps:
        print(f"  Temperatures captured: {len(temps)}")
        print(f"  Range: {min(temps)} deg F ({fahrenheit_to_celsius(min(temps))} deg C) "
              f"to {max(temps)} deg F ({fahrenheit_to_celsius(max(temps))} deg C)")
        print(f"  Values: {', '.join(str(t) for t in temps)}")
    else:
        print("  No temperatures captured yet.")


if __name__ == "__main__":
    main()

