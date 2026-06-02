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
    from custom_components.joyonway.protocol import find_frames, unescape_frame, is_broadcast
    from custom_components.joyonway.adapters.p25b85 import P25B85Adapter
except ImportError:
    print("Error: Could not import custom component. Make sure you run this script from the repository root.")
    sys.exit(1)

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
    water = data.get("water_temperature")
    setp = data.get("setpoint")
    jets = data.get("jets", "unknown")
    status = data.get("status", "unknown")
    heater_enabled = "ON" if data.get("heater_enabled") else "OFF"
    h_byte = data.get("heater_byte_raw", 0)
    p_byte = data.get("pump_byte_raw", 0)
    l_byte = data.get("light_cycle_byte_raw", 0)
    
    print(f"\r  [Current State] Temp: {water}°C/{setp}°C | Jets: {jets:<4} | Heater: {heater_enabled:<3} | Status: {status:<12} (h=0x{h_byte:02X}, p=0x{p_byte:02X}, l=0x{l_byte:02X})", end="", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Interactive guided capture tool.")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bridge host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bridge port (default: {DEFAULT_PORT})")
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
    print("\nRunbook steps:")
    print("  1. Enable the heater.")
    print("  2. Wait for circulation to start.")
    print("  3. Set jets to LOW (wait 5s).")
    print("  4. Set jets to HIGH (wait 5s).")
    print("  5. Set jets to LOW (wait 5s).")
    print("  6. Wait for the heater to start (heating).")
    print("  7. Stop the heating (disable heater).")
    print("  8. Wait for circulation to show up again (postheating).")
    print("  9. Stop the jets.")
    print("\nPress ENTER when you are ready to start the capture.")
    input()
    
    print("Capture started. Logging raw bytes...")
    
    raw_buffer = bytearray()
    stream_buffer = bytearray()
    
    current_step = 1
    step_start_time = time.monotonic()
    
    # State tracking
    last_status = None
    
    # Runbook Step descriptions and trigger criteria
    # Returns (success_bool, next_step_instructions)
    def check_step_transition(step: int, data: dict) -> tuple[bool, str | None]:
        jets = data.get("jets")
        status = data.get("status")
        heater_enabled = data.get("heater_enabled", False)
        h_raw = data.get("heater_byte_raw", 0)
        p_raw = data.get("pump_byte_raw", 0)
        l_raw = data.get("light_cycle_byte_raw", 0)
        
        heater_base = h_raw & ~0x08
        heating_cycle_active = bool(l_raw & 0x80)

        if step == 1:
            # Enable the heater
            if heater_enabled:
                return True, "Heater enabled detected! Next step: Wait for the circulation to start."
        elif step == 2:
            # Wait for circulation to start (0x51 in heater state)
            if status == "circulation" or heater_base == 0x51:
                return True, "Circulation started detected! Next step: Set the jets to LOW speed on the panel."
        elif step == 3:
            # Set the jets to low
            if jets == "low" or p_raw == 0x02:
                print("\n  --> Jets are LOW. Capturing steady state for 5 seconds...")
                time.sleep(5.0)
                return True, "Next step: Set the jets to HIGH speed on the panel."
        elif step == 4:
            # Set the jets to high
            if jets == "high" or p_raw == 0x04:
                print("\n  --> Jets are HIGH. Capturing steady state for 5 seconds...")
                time.sleep(5.0)
                return True, "Next step: Set the jets back to LOW speed on the panel."
        elif step == 5:
            # Set the jets to low
            if jets == "low" or p_raw == 0x02:
                print("\n  --> Jets are LOW again. Capturing steady state for 5 seconds...")
                time.sleep(5.0)
                return True, "Next step: Wait for the heater to start (heating mode)."
        elif step == 6:
            # Wait for the heater to start (heating status / 0x55 or 0x54)
            if status == "heating" or heater_base in (0x55, 0x54):
                return True, "Heater is now actively heating! Next step: Stop the heating (disable the heater)."
        elif step == 7:
            # Stop the heating
            if not heater_enabled:
                return True, "Heater disabled detected! Next step: Wait for the post-heating circulation to show up (circle icon)."
        elif step == 8:
            # Wait for circulation (postheating: base is 0x40 but heating cycle flag is active)
            if status == "circulation" and heater_base == 0x40 and heating_cycle_active:
                return True, "Post-heating circulation detected! Next step: Stop the jets (turn them off)."
        elif step == 9:
            # Stop the jets
            if jets == "off" or p_raw == 0x00:
                return True, "Jets are turned off! Capture complete."
                
        return False, None

    print(f"\n[STEP 1/9] Please enable the heater on the touchpad or Home Assistant.")
    
    last_read_time = time.monotonic()
    
    try:
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    print("\nConnection closed by bridge.")
                    break
                raw_buffer.extend(chunk)
                stream_buffer.extend(chunk)
                last_read_time = time.monotonic()
            except BlockingIOError:
                # No data ready right now
                if time.monotonic() - last_read_time > 15.0:
                    print("\nWarning: No data received from bridge for 15 seconds. Is the spa broadcasting?")
                    last_read_time = time.monotonic()
                time.sleep(0.05)
                
            # Process frames in stream buffer
            frames = find_frames(bytes(stream_buffer))
            if frames:
                # Clear from stream buffer whatever we parsed
                # (keep remaining partial frame bytes)
                last_frame = frames[-1]
                idx = stream_buffer.rfind(last_frame)
                if idx != -1:
                    del stream_buffer[: idx + len(last_frame)]
                    
                # Look at the latest broadcast frame
                broadcasts = [f for f in frames if is_broadcast(f)]
                if broadcasts:
                    logical = unescape_frame(broadcasts[-1])
                    parsed = adapter.parse_status(logical)
                    if parsed:
                        print_status(parsed)
                        
                        # Check step transition
                        success, next_inst = check_step_transition(current_step, parsed)
                        if success:
                            current_step += 1
                            if current_step <= 9:
                                print(f"\n\n[STEP {current_step}/9] {next_inst}")
                            else:
                                print(f"\n\nRunbook completed successfully!")
                                break
                                
            time.sleep(0.01)
            
    except KeyboardInterrupt:
        print("\nCapture interrupted by user.")
    finally:
        sock.close()
        
    # Write to file
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"combined_jets_circulation_{timestamp}.bin"
    output_dir = ROOT / "tools" / "captures" / "heating"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / filename
    
    try:
        with open(output_path, "wb") as f:
            f.write(raw_buffer)
        print(f"\nSuccessfully wrote {len(raw_buffer)} raw bytes to:")
        print(f"  {output_path.absolute()}")
    except Exception as e:
        print(f"Error writing file: {e}")
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
