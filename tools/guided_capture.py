#!/usr/bin/env python3
"""Interactive guided capture script for Joyonway spa RS-485 bus.

Guides the user step-by-step through the runbook to capture the combined
jets and circulation states, parsing broadcasts in real-time and writing
the raw binary output to a capture file.
"""

from __future__ import annotations

import argparse
import datetime
import os
import socket
import sys
import time
from pathlib import Path

# Add repository root to path so we can import protocol/adapter
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from custom_components.joyonway.protocol import (
        find_frames,
        unescape_frame,
        is_broadcast,
    )
    from custom_components.joyonway.adapters.p25 import P25B85Adapter
except ImportError:
    print(
        "Error: Could not import custom component. Make sure you run this script from the repository root."
    )
    sys.path.insert(0, str(ROOT / "custom_components"))
    from joyonway.protocol import find_frames, unescape_frame, is_broadcast
    from joyonway.adapters.p25 import P25B85Adapter


# Load .env if present
def _load_dotenv():
    env_path = ROOT / ".env"
    if env_path.is_file():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

DEFAULT_HOST = os.environ.get("SPA_BRIDGE_HOST", "192.168.188.58")
DEFAULT_PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))


def print_status(data: dict) -> None:
    """Print a one-line summary of current status."""
    water = data.get("current_temperature")
    setp = data.get("setpoint")
    jets = data.get("jets", "unknown")
    status = data.get("status", "unknown")
    heater_enabled = "ON" if data.get("heater_enabled") else "OFF"
    h_byte = data.get("heater_byte_raw", 0)
    p_byte = data.get("jets_byte_raw", 0)
    l_byte = data.get("light_cycle_byte_raw", 0)

    print(
        f"\r  [Current State] Temp: {water}°C/{setp}°C | Jets: {jets:<4} | Heater: {heater_enabled:<3} | Status: {status:<12} (h=0x{h_byte:02X}, p=0x{p_byte:02X}, l=0x{l_byte:02X})",
        end="",
        flush=True,
    )


def run_jets_sequence(
    sock: socket.socket, adapter: P25B85Adapter
) -> tuple[bytearray, bool]:
    """Guide the user through all jets transitions (off->low->high->low->off->high->off)."""
    print("\nStarting Jets Transition Runbook:")
    print("  Step 1: Ensure jets are initially OFF.")
    print("  Step 2: Turn jets LOW (press button once).")
    print("  Step 3: Turn jets HIGH (press button once).")
    print("  Step 4: Turn jets LOW (press button twice: high -> off -> low).")
    print("  Step 5: Turn jets OFF (press button twice: low -> high -> off).")
    print("  Step 6: Turn jets HIGH (press button twice: off -> low -> high).")
    print("  Step 7: Turn jets OFF (press button once: high -> off).")
    print("\nPress ENTER when you are ready to start.")
    input()

    raw_buffer = bytearray()
    stream_buffer = bytearray()

    current_step = 1
    last_read_time = time.monotonic()

    # Runbook Step descriptions and trigger criteria
    def check_step_transition(step: int, data: dict) -> tuple[bool, str | None]:
        jets = data.get("jets")
        p_raw = data.get("jets_byte_raw", 0)

        if step == 1:
            if jets == "off" or p_raw == 0x00:
                return (
                    True,
                    "Jets are confirmed OFF! Step 2: Turn the jets LOW (press button once).",
                )
        elif step == 2:
            if jets == "low" or p_raw == 0x02:
                print(
                    "\n  --> Jets LOW detected. Capturing steady state for 3 seconds..."
                )
                time.sleep(3.0)
                return True, "Step 3: Turn the jets HIGH (press button once)."
        elif step == 3:
            if jets == "high" or p_raw == 0x04:
                print(
                    "\n  --> Jets HIGH detected. Capturing steady state for 3 seconds..."
                )
                time.sleep(3.0)
                return (
                    True,
                    "Step 4: Turn the jets LOW (press button twice: high -> off -> low).",
                )
        elif step == 4:
            if jets == "low" or p_raw == 0x02:
                print(
                    "\n  --> Jets LOW detected. Capturing steady state for 3 seconds..."
                )
                time.sleep(3.0)
                return (
                    True,
                    "Step 5: Turn the jets OFF (press button twice: low -> high -> off).",
                )
        elif step == 5:
            if jets == "off" or p_raw == 0x00:
                print(
                    "\n  --> Jets OFF detected. Capturing steady state for 3 seconds..."
                )
                time.sleep(3.0)
                return (
                    True,
                    "Step 6: Turn the jets HIGH (press button twice: off -> low -> high).",
                )
        elif step == 6:
            if jets == "high" or p_raw == 0x04:
                print(
                    "\n  --> Jets HIGH detected. Capturing steady state for 3 seconds..."
                )
                time.sleep(3.0)
                return (
                    True,
                    "Step 7: Turn the jets OFF (press button once: high -> off).",
                )
        elif step == 7:
            if jets == "off" or p_raw == 0x00:
                return True, "Jets are confirmed OFF! Sequence complete."

        return False, None

    print("\n[STEP 1/7] Waiting for jets to be OFF...")

    try:
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    return raw_buffer, False
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                if time.monotonic() - last_read_time > 15.0:
                    print("\nWarning: No data received from bridge for 15 seconds.")
                    last_read_time = time.monotonic()
                time.sleep(0.05)

            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]

                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
                        success, next_inst = check_step_transition(current_step, parsed)
                        if success:
                            current_step += 1
                            if current_step <= 7:
                                print(f"\n\n[STEP {current_step}/7] {next_inst}")
                            else:
                                print(
                                    "\n\nJets transition runbook completed successfully!"
                                )
                                return raw_buffer, True
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nJets runbook interrupted by user.")
        return raw_buffer, False


def run_heating_sequence(
    sock: socket.socket, adapter: P25B85Adapter
) -> tuple[bytearray, bool]:
    """Original runbook: guide user through heating & circulation states."""
    print("\nStarting Heating & Circulation Runbook:")
    print("  1. Enable the heater.")
    print("  2. Wait for circulation to start.")
    print("  3. Set jets to LOW (wait 5s).")
    print("  4. Set jets to HIGH (wait 5s).")
    print("  5. Set jets to LOW (wait 5s).")
    print("  6. Wait for the heater to start (heating).")
    print("  7. Stop the heating (disable heater).")
    print("  8. Wait for circulation to show up again (postheating).")
    print("  9. Stop the jets.")
    print("\nPress ENTER when you are ready to start.")
    input()

    raw_buffer = bytearray()
    stream_buffer = bytearray()

    current_step = 1
    last_read_time = time.monotonic()

    def check_step_transition(step: int, data: dict) -> tuple[bool, str | None]:
        jets = data.get("jets")
        status = data.get("status")
        heater_enabled = data.get("heater_enabled", False)
        h_raw = data.get("heater_byte_raw", 0)
        p_raw = data.get("jets_byte_raw", 0)
        l_raw = data.get("light_cycle_byte_raw", 0)

        heater_base = h_raw & ~0x08
        heating_cycle_active = bool(l_raw & 0x80)

        if step == 1:
            if heater_enabled:
                return (
                    True,
                    "Heater enabled detected! Next step: Wait for the circulation to start.",
                )
        elif step == 2:
            if status == "circulation" or heater_base == 0x51:
                return (
                    True,
                    "Circulation started detected! Next step: Set the jets to LOW speed on the panel.",
                )
        elif step == 3:
            if jets == "low" or p_raw == 0x02:
                print("\n  --> Jets are LOW. Capturing steady state for 5 seconds...")
                time.sleep(5.0)
                return True, "Next step: Set the jets to HIGH speed on the panel."
        elif step == 4:
            if jets == "high" or p_raw == 0x04:
                print("\n  --> Jets are HIGH. Capturing steady state for 5 seconds...")
                time.sleep(5.0)
                return True, "Next step: Set the jets back to LOW speed on the panel."
        elif step == 5:
            if jets == "low" or p_raw == 0x02:
                print(
                    "\n  --> Jets are LOW again. Capturing steady state for 5 seconds..."
                )
                time.sleep(5.0)
                return True, "Next step: Wait for the heater to start (heating mode)."
        elif step == 6:
            if status == "heating" or heater_base in (0x55, 0x54):
                return (
                    True,
                    "Heater is now actively heating! Next step: Stop the heating (disable the heater).",
                )
        elif step == 7:
            if not heater_enabled:
                return (
                    True,
                    "Heater disabled detected! Next step: Wait for the post-heating circulation to show up (circle icon).",
                )
        elif step == 8:
            if status == "circulation" and heater_base == 0x40 and heating_cycle_active:
                return (
                    True,
                    "Post-heating circulation detected! Next step: Stop the jets (turn them off).",
                )
        elif step == 9:
            if jets == "off" or p_raw == 0x00:
                return True, "Jets are turned off! Capture complete."

        return False, None

    print("\n[STEP 1/9] Please enable the heater on the touchpad or Home Assistant.")

    try:
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    return raw_buffer, False
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                if time.monotonic() - last_read_time > 15.0:
                    print("\nWarning: No data received from bridge for 15 seconds.")
                    last_read_time = time.monotonic()
                time.sleep(0.05)

            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]

                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
                        success, next_inst = check_step_transition(current_step, parsed)
                        if success:
                            current_step += 1
                            if current_step <= 9:
                                print(f"\n\n[STEP {current_step}/9] {next_inst}")
                            else:
                                print("\n\nRunbook completed successfully!")
                                return raw_buffer, True
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nHeating runbook interrupted by user.")
        return raw_buffer, False


def run_heater_mode_sequence(
    sock: socket.socket, adapter: P25B85Adapter
) -> tuple[bytearray, bool]:
    """Guide the user through capturing heater mode settings (auto -> manual -> auto)."""
    print("\nStarting Heater Mode Transition Runbook:")
    print("  We will capture the transitions between Auto and Manual heater modes.")
    print(
        "  Step 1: Ensure heater mode is currently set to MANUAL on the physical touchpad."
    )
    print("  Step 2: Change heater mode to AUTO.")
    print("  Step 3: Change heater mode back to MANUAL.")

    print("\nPress ENTER when the spa is in MANUAL mode and you are ready to start.")
    input()

    raw_buffer = bytearray()
    stream_buffer = bytearray()

    try:
        # Step 1: Capture baseline MANUAL mode for 5 seconds
        print("\n[STEP 1/3] Capturing baseline MANUAL mode for 5 seconds...")
        start_time = time.monotonic()
        last_read_time = time.monotonic()
        while time.monotonic() - start_time < 5.0:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    return raw_buffer, False
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                if time.monotonic() - last_read_time > 15.0:
                    print("\nWarning: No data received from bridge for 15 seconds.")
                    last_read_time = time.monotonic()
                time.sleep(0.05)

            # Display status on screen so the user sees it is alive
            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]
                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
            time.sleep(0.01)

        print(
            "\n\n[STEP 2/3] Action: Please change the heater mode to AUTO on the touchpad."
        )
        print("Press ENTER immediately AFTER you have changed it to AUTO.")
        input()

        # Capture AUTO mode for 5 seconds
        print("\nCapturing AUTO mode for 5 seconds...")
        start_time = time.monotonic()
        last_read_time = time.monotonic()
        while time.monotonic() - start_time < 5.0:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    return raw_buffer, False
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                time.sleep(0.05)

            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]
                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
            time.sleep(0.01)

        print(
            "\n\n[STEP 3/3] Action: Please change the heater mode back to MANUAL on the touchpad."
        )
        print("Press ENTER immediately AFTER you have changed it back to MANUAL.")
        input()

        # Capture MANUAL mode for 5 seconds
        print("\nCapturing MANUAL mode for 5 seconds...")
        start_time = time.monotonic()
        last_read_time = time.monotonic()
        while time.monotonic() - start_time < 5.0:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    return raw_buffer, False
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                time.sleep(0.05)

            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]
                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
            time.sleep(0.01)

        print("\n\nHeater mode transition runbook completed successfully!")
        return raw_buffer, True
    except KeyboardInterrupt:
        print("\nHeater mode runbook interrupted by user.")
        return raw_buffer, False


def run_monitor(sock: socket.socket, adapter: P25B85Adapter) -> None:
    """Monitor broadcast frames continuously (no file logging)."""
    print("\nMonitoring broadcasts in real-time. Press Ctrl+C to stop.\n")
    stream_buffer = bytearray()
    last_read_time = time.monotonic()

    try:
        # Clear any initial buffer buildup
        sock.setblocking(False)
        try:
            while sock.recv(4096):
                pass
        except BlockingIOError:
            pass

        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    break
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                if time.monotonic() - last_read_time > 15.0:
                    print("\nWarning: No data received for 15 seconds.")
                    last_read_time = time.monotonic()
                time.sleep(0.05)

            frames = find_frames(bytes(stream_buffer))
            if frames:
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]

                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive guided capture tool.")
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help=f"Bridge host (default: {DEFAULT_HOST})"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Bridge port (default: {DEFAULT_PORT})",
    )
    args = parser.parse_args()

    adapter = P25B85Adapter()

    print("=" * 80)
    print("Joyonway Guided Capture Tool")
    print(f"Connecting to: {args.host}:{args.port}")
    print("=" * 80)

    try:
        sock = socket.create_connection((args.host, args.port), timeout=10.0)
    except Exception as e:
        print(f"Error: Could not connect to bridge: {e}")
        return 1

    sock.setblocking(False)
    print("Connected successfully!")

    while True:
        print("\nSelect a mode:")
        print(
            "  1) Capture Jets Transitions Runbook (OFF -> LOW -> HIGH -> LOW -> OFF -> HIGH -> OFF)"
        )
        print("  2) Capture Heating & Circulation Sequence Runbook (Original)")
        print("  3) Capture Heater Mode Runbook (MANUAL -> AUTO -> MANUAL)")
        print("  4) Monitor broadcasts in real-time (no logging)")
        print("  0) Exit")

        choice = input("Option [0-4]: ").strip()
        if choice == "0":
            sock.close()
            print("Exiting.")
            return 0
        elif choice == "4":
            run_monitor(sock, adapter)
        elif choice in ("1", "2", "3"):
            ok = False
            raw_buffer = bytearray()
            seq_name = ""

            # Clear any initial buffer buildup before starting capture
            try:
                while sock.recv(4096):
                    pass
            except BlockingIOError:
                pass

            if choice == "1":
                raw_buffer, ok = run_jets_sequence(sock, adapter)
                seq_name = "jets"
            elif choice == "2":
                raw_buffer, ok = run_heating_sequence(sock, adapter)
                seq_name = "heating"
            else:
                raw_buffer, ok = run_heater_mode_sequence(sock, adapter)
                seq_name = "heater_mode"

            if ok and len(raw_buffer) > 0:
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"{seq_name}_transitions_{timestamp}.bin"
                output_dir = ROOT / "tools" / "captures" / seq_name
                output_dir.mkdir(parents=True, exist_ok=True)
                output_path = output_dir / filename

                try:
                    with open(output_path, "wb") as f:
                        f.write(raw_buffer)
                    print(f"\nSuccessfully wrote {len(raw_buffer)} raw bytes to:")
                    print(f"  {output_path.absolute()}")
                except Exception as e:
                    print(f"Error writing file: {e}")
            else:
                print("\nNo capture file written (sequence incomplete or aborted).")
        else:
            print("Invalid option.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
