"""Monitor byte 14 (heater state) transitions during a heating cycle.

Usage:
    python3 tools/capture_heating_cycle.py

Connects to the EW11 bridge and logs every change in byte 14 (heater state)
and byte 12 (pump) with timestamps. Run this while triggering a heating cycle
to capture all intermediate states.

Press Ctrl+C to stop and save results.
"""
import os
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "custom_components" / "joyonway_p25b85"))

from protocol import find_frames, unescape_frame
from adapters.p25b85 import (
    P25B85_SIGNATURE,
    IDX_PUMP_BYTE,
    IDX_HEATER_STATE,
    IDX_WATER_TEMP,
    IDX_SETPOINT,
    HEATER_STATE_MAP,
    MASK_HEATER_BLOWER,
)

# Load .env for bridge IP
env_path = Path(__file__).resolve().parents[1] / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HOST = os.environ.get("EW11_IP", os.environ.get("SPA_HOST", ""))
PORT = int(os.environ.get("EW11_PORT", os.environ.get("SPA_PORT", "8899")))

if not HOST:
    print("ERROR: Set EW11_IP in .env or environment")
    sys.exit(1)

PUMP_MAP = {0x00: "off", 0x02: "low", 0x04: "high"}

def heater_label(byte_val):
    base = byte_val & ~MASK_HEATER_BLOWER
    blower = " +blower" if byte_val & MASK_HEATER_BLOWER else ""
    name = HEATER_STATE_MAP.get(base, "unknown")
    return f"0x{byte_val:02X} ({name}{blower})"

def pump_label(byte_val):
    return f"0x{byte_val:02X} ({PUMP_MAP.get(byte_val, '???')})"

def main():
    print(f"Connecting to {HOST}:{PORT}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((HOST, PORT))
    sock.settimeout(30)
    print("Connected. Monitoring byte 14 (heater) and byte 12 (pump) transitions.")
    print("Trigger the heating cycle now. Press Ctrl+C to stop.\n")
    print(f"{'Time':<12} {'Elapsed':>8}  {'Byte14 (heater)':<28} {'Byte12 (pump)':<18} {'Water':>5} {'Set':>3}")
    print("-" * 90)

    buffer = b""
    last_heater = None
    last_pump = None
    start_time = time.time()
    transitions = []
    frame_count = 0

    try:
        while True:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                print("  [timeout - no data for 30s]")
                continue
            if not data:
                print("  [connection closed]")
                break

            buffer += data
            frames = find_frames(buffer)

            # Keep unprocessed tail
            if frames:
                last_end = buffer.rfind(b'\x1d') + 1
                buffer = buffer[last_end:]

            for raw in frames:
                logical = unescape_frame(raw, full=True)
                if len(logical) < 30:
                    continue
                if logical[: len(P25B85_SIGNATURE)] != P25B85_SIGNATURE:
                    continue

                frame_count += 1
                heater = logical[IDX_HEATER_STATE]
                pump = logical[IDX_PUMP_BYTE]
                water_f = logical[IDX_WATER_TEMP]
                setpoint_f = logical[IDX_SETPOINT]
                water_c = round((water_f - 32) * 5 / 9) if water_f > 0 else "?"
                setpoint_c = round((setpoint_f - 32) * 5 / 9) if setpoint_f > 0 else "?"

                if heater != last_heater or pump != last_pump:
                    now = datetime.now().strftime("%H:%M:%S")
                    elapsed = f"{time.time() - start_time:.1f}s"
                    h_label = heater_label(heater)
                    p_label = pump_label(pump)

                    marker = ""
                    if last_heater is not None:
                        marker = " ◀ CHANGE"

                    print(f"{now:<12} {elapsed:>8}  {h_label:<28} {p_label:<18} {water_c:>5} {setpoint_c:>3}{marker}")

                    transitions.append({
                        "time": now,
                        "elapsed": elapsed,
                        "heater": heater,
                        "pump": pump,
                        "water_c": water_c,
                        "setpoint_c": setpoint_c,
                    })

                    last_heater = heater
                    last_pump = pump

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        elapsed_total = time.time() - start_time
        print(f"\n{'=' * 90}")
        print(f"Capture complete. Duration: {elapsed_total:.0f}s, Frames: {frame_count}")
        print(f"Transitions recorded: {len(transitions)}")
        print("\nSummary of all byte 14 values seen:")
        seen = set()
        for t in transitions:
            seen.add(t["heater"])
        for v in sorted(seen):
            print(f"  {heater_label(v)}")

        # Save to file
        out_path = Path(__file__).resolve().parent / "captures" / f"heating_cycle_{datetime.now().strftime('%H%M%S')}.log"
        out_path.parent.mkdir(exist_ok=True)
        with open(out_path, "w") as f:
            f.write(f"Heating cycle capture - {datetime.now().isoformat()}\n")
            f.write(f"Duration: {elapsed_total:.0f}s, Frames: {frame_count}\n\n")
            f.write(f"{'Time':<12} {'Elapsed':>8}  {'Byte14':<8} {'Byte12':<8} {'Water':>5} {'Set':>3}  Label\n")
            f.write("-" * 70 + "\n")
            for t in transitions:
                f.write(f"{t['time']:<12} {t['elapsed']:>8}  0x{t['heater']:02X}    0x{t['pump']:02X}    {t['water_c']:>5} {t['setpoint_c']:>3}  {heater_label(t['heater'])}\n")
        print(f"\nLog saved to: {out_path}")


if __name__ == "__main__":
    main()

