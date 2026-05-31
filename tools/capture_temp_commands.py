#!/usr/bin/env python3
"""
Phase 4b — Temperature command frame capture (all setpoints).

Captures one command frame per degree by having you press the UP or DOWN
button once per capture window. Fully automated timing — you only press
Enter once to start, then follow the audio/visual prompts.

Strategy:
  1. Set spa to minimum (10°C / 50°F) manually before starting
  2. Run UP sequence: 30 presses (10°C → 40°C), one per window
  3. Run DOWN sequence: 30 presses (40°C → 10°C), one per window

Each window: 15s capture. Press the button ~3s in. The script shows
the captured command frame immediately so you can verify it's working.

Abort with Ctrl-C at any time — progress is saved.
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

__version__ = "1.0.0"

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


def celsius_to_fahrenheit(c: float) -> int:
    return round(c * 9 / 5 + 32)


def main():
    parser = argparse.ArgumentParser(
        description="Capture temperature command frames for all setpoints (10-40°C)",
    )
    parser.add_argument("--host", default=_DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    parser.add_argument("--duration", type=float, default=15.0,
                        help="Capture duration per step (default: 15s)")
    parser.add_argument("--direction", choices=["up", "down", "both"], default="both",
                        help="Capture direction: up, down, or both (default: both)")
    parser.add_argument("--start-temp-c", type=int, default=10,
                        help="Starting temperature in °C (default: 10)")
    parser.add_argument("--end-temp-c", type=int, default=40,
                        help="Ending temperature in °C (default: 40)")
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
        print(f"Loaded {len(existing)} existing temperature commands.")

    print()
    print("=" * 66)
    print("  Joyonway P25B85 — Temperature Command Capture")
    print("=" * 66)
    print()
    print("This captures one command frame per °C from 10°C to 40°C.")
    print()
    print("HOW IT WORKS:")
    print("  1. You press Enter ONCE to start")
    print(f"  2. Each step: {args.duration:.0f}s capture window")
    print("  3. When you see '>>> PRESS NOW <<<', press the button ONCE")
    print("  4. Wait for the result, then get ready for the next one")
    print("  5. Ctrl-C to abort (progress is saved)")
    print()
    print("PREPARATION:")
    print(f"  • Set spa to {args.start_temp_c}°C ({celsius_to_fahrenheit(args.start_temp_c)}°F) BEFORE starting")
    print("  • Make sure no other client is connected to the EW11")
    print()
    print(f"  Bridge: {args.host}:{args.port}")
    print(f"  Output: {os.path.abspath(args.out_dir)}")
    print()

    if args.dry_run:
        print("🧪 DRY-RUN MODE\n")

    # Build the step sequences
    steps: list[tuple[str, int]] = []  # (direction, target_temp_F)

    start_f = celsius_to_fahrenheit(args.start_temp_c)
    end_f = celsius_to_fahrenheit(args.end_temp_c)

    if args.direction in ("up", "both"):
        # UP: from start+1 to end (pressing UP each time)
        for target_f in range(start_f + 1, end_f + 1):
            steps.append(("up", target_f))

    if args.direction in ("down", "both"):
        # DOWN: from end-1 to start (pressing DOWN each time)
        for target_f in range(end_f - 1, start_f - 1, -1):
            steps.append(("down", target_f))

    # Filter out already-captured temperatures
    remaining = [(d, t) for d, t in steps if f"{t}F_{d}" not in existing]

    if not remaining:
        print("✅ All temperatures already captured!")
        sys.exit(0)

    total = len(remaining)
    print(f"Steps to capture: {total} ({len(steps) - total} already done)")
    print()

    try:
        input("Press Enter to start the sequence... ")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)

    # First, capture a baseline (no button press) to identify normal bus traffic
    print("\n  ⏺  Capturing baseline (don't press anything)...", end="", flush=True)
    if args.dry_run:
        baseline_data = b""
    else:
        baseline_data = capture_raw(args.host, args.port, 5.0)
    baseline_frames = set(f.hex() for f in extract_all_frames(baseline_data)
                         if len(f) > 1 and f[1] != 0xFF)
    print(f" done ({len(baseline_frames)} baseline frame types)")

    # Set up Ctrl-C handler
    interrupted = False
    def signal_handler(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n\n⚠️  Interrupted! Saving progress...")

    signal.signal(signal.SIGINT, signal_handler)

    captured_count = 0

    for step_idx, (direction, target_f) in enumerate(remaining):
        if interrupted:
            break

        target_c = fahrenheit_to_celsius(target_f)
        arrow = "⬆️ " if direction == "up" else "⬇️ "
        button = "UP" if direction == "up" else "DOWN"

        print(f"\n{'━' * 66}")
        print(f"  Step {step_idx + 1}/{total}:  {arrow} Press {button}  →  "
              f"Target: {target_f}°F ({target_c}°C)")
        print(f"{'━' * 66}")

        # Countdown before capture
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
        print(f"  ⏺  CAPTURING ({args.duration:.0f}s) — ", end="", flush=True)
        time.sleep(2)  # 2 second delay before the prompt
        print(">>> PRESS {button} NOW! <<<".format(button=button), end="", flush=True)

        if args.dry_run:
            time.sleep(args.duration - 2)
            raw_data = b""
            new_frames = []
        else:
            raw_data = capture_raw(args.host, args.port, args.duration - 2)
            new_frames = extract_command_frames(raw_data, baseline_frames)

        print(" done!")

        # Analyze
        # Look for 22-byte command frames addressed to 0x01 (our known command format)
        cmd_frames = [f for f in new_frames if len(f) == 22 and f[1] == 0x01]

        if cmd_frames:
            # Pick the first unique command frame
            frame = cmd_frames[0]
            hex_str = frame.hex()
            # Extract the setpoint byte (position 15 in our known frame structure)
            setpoint_byte = frame[15]

            print(f"  ✅ Command frame captured!")
            print(f"     Frame: {hex_str}")
            print(f"     Setpoint byte[15] = 0x{setpoint_byte:02X} ({setpoint_byte}°F = "
                  f"{fahrenheit_to_celsius(setpoint_byte)}°C)")

            if setpoint_byte != target_f:
                print(f"     ⚠️  Expected {target_f}°F but got {setpoint_byte}°F in frame!")

            # Save
            key = f"{target_f}F_{direction}"
            existing[key] = hex_str
            captured_count += 1

            # Also save by temperature for easy lookup
            existing[f"{target_f}F"] = hex_str

        elif new_frames:
            print(f"  ⚠️  Found {len(new_frames)} non-broadcast frames but none match "
                  f"22-byte command format:")
            for nf in new_frames[:3]:
                print(f"     [{len(nf)}B] {nf.hex()}")
        else:
            print(f"  ❌ No new command frame detected! (Did you press the button?)")
            print(f"     Raw: {len(raw_data)} bytes captured")

        # Save progress after each step
        with open(results_file, "w") as f:
            json.dump(existing, f, indent=2)

    # Final summary
    print(f"\n\n{'━' * 66}")
    print(f"  Session complete! Captured {captured_count} new temperature commands.")
    print(f"  Total in library: {len([k for k in existing if k.endswith('F') and '_' not in k])}")
    print(f"  Saved to: {results_file}")
    print(f"{'━' * 66}")

    # Show coverage
    print("\n  Temperature coverage:")
    for f_temp in range(start_f, end_f + 1):
        key = f"{f_temp}F"
        c_temp = fahrenheit_to_celsius(f_temp)
        if key in existing:
            print(f"    ✅ {f_temp}°F ({c_temp}°C)")
        else:
            print(f"    ❌ {f_temp}°F ({c_temp}°C)")

    print()


if __name__ == "__main__":
    main()

