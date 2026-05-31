"""Capture ALL frames during a heating cycle for full byte-by-byte analysis.

Usage:
    python3 tools/capture_heating_cycle.py

Connects to the EW11 bridge and saves EVERY broadcast frame (full hex payload)
with timestamps to a JSONL file. Also prints byte 14/12 transitions to console.

This allows post-hoc analysis of ANY byte that changes during the heating cycle,
not just the ones we already know about.

Press Ctrl+C to stop and save results.
"""
import json
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

HOST = os.environ.get("SPA_BRIDGE_HOST", os.environ.get("SPA_HOST", ""))
PORT = int(os.environ.get("SPA_BRIDGE_PORT", os.environ.get("SPA_PORT", "8899")))

if not HOST:
    print("ERROR: Set SPA_BRIDGE_HOST in .env or environment")
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
    print("Connected. Capturing ALL frames + monitoring transitions.")
    print("Trigger the heating cycle now. Press Ctrl+C to stop.\n")
    print(f"{'Time':<14} {'Elapsed':>8}  {'Byte14 (heater)':<28} {'Byte12 (pump)':<18} {'Water':>5} {'Set':>3}")
    print("-" * 90)

    # Output file — JSONL with every frame
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_dir = Path(__file__).resolve().parent / "captures_heating"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"heating_cycle_{timestamp_str}.jsonl"
    out_file = open(out_path, "w")
    print(f"Recording all frames to: {out_path}\n")

    buffer = b""
    last_heater = None
    last_pump = None
    last_frame_bytes = None
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
                now_ts = time.time()
                now_str = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                elapsed = now_ts - start_time

                heater = logical[IDX_HEATER_STATE]
                pump = logical[IDX_PUMP_BYTE]
                water_f = logical[IDX_WATER_TEMP]
                setpoint_f = logical[IDX_SETPOINT]
                water_c = round((water_f - 32) * 5 / 9) if water_f > 0 else None
                setpoint_c = round((setpoint_f - 32) * 5 / 9) if setpoint_f > 0 else None

                # Find bytes that changed from previous frame
                changed_bytes = {}
                if last_frame_bytes is not None:
                    for i in range(min(len(logical), len(last_frame_bytes))):
                        if logical[i] != last_frame_bytes[i]:
                            changed_bytes[i] = {"old": last_frame_bytes[i], "new": logical[i]}

                # Save EVERY frame as JSONL
                record = {
                    "frame": frame_count,
                    "time": now_str,
                    "elapsed_s": round(elapsed, 2),
                    "hex": logical.hex(),
                    "len": len(logical),
                    "byte12_pump": f"0x{pump:02X}",
                    "byte14_heater": f"0x{heater:02X}",
                    "water_c": water_c,
                    "setpoint_c": setpoint_c,
                }
                if changed_bytes:
                    # Record which byte indices changed (compact format)
                    record["changed"] = {
                        str(k): f"0x{v['old']:02X}->0x{v['new']:02X}"
                        for k, v in changed_bytes.items()
                    }
                out_file.write(json.dumps(record) + "\n")

                # Print transitions to console
                if heater != last_heater or pump != last_pump:
                    elapsed_str = f"{elapsed:.1f}s"
                    h_label = heater_label(heater)
                    p_label = pump_label(pump)

                    marker = ""
                    if last_heater is not None:
                        marker = " ◀ CHANGE"

                    print(f"{now_str:<14} {elapsed_str:>8}  {h_label:<28} {p_label:<18} {water_c:>5} {setpoint_c:>3}{marker}")

                    transitions.append({
                        "time": now_str,
                        "elapsed": elapsed_str,
                        "heater": heater,
                        "pump": pump,
                        "water_c": water_c,
                        "setpoint_c": setpoint_c,
                        "frame_num": frame_count,
                    })

                    last_heater = heater
                    last_pump = pump

                last_frame_bytes = logical

    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
        out_file.close()
        elapsed_total = time.time() - start_time
        print(f"\n{'=' * 90}")
        print(f"Capture complete. Duration: {elapsed_total:.0f}s, Frames: {frame_count}")
        print(f"Transitions recorded: {len(transitions)}")
        print(f"\nAll frames saved to: {out_path}")
        print("\nSummary of all byte 14 values seen:")
        seen = set()
        for t in transitions:
            seen.add(t["heater"])
        for v in sorted(seen):
            print(f"  {heater_label(v)}")

        # Also save a human-readable summary
        summary_path = out_path.with_suffix(".summary.txt")
        with open(summary_path, "w") as f:
            f.write(f"Heating cycle capture - {datetime.now().isoformat()}\n")
            f.write(f"Duration: {elapsed_total:.0f}s, Frames: {frame_count}\n")
            f.write(f"Full frame data: {out_path.name}\n\n")
            f.write(f"{'Time':<14} {'Elapsed':>8}  {'Byte14':<8} {'Byte12':<8} {'Water':>5} {'Set':>3}  {'Frame#':>6}  Label\n")
            f.write("-" * 80 + "\n")
            for t in transitions:
                f.write(f"{t['time']:<14} {t['elapsed']:>8}  0x{t['heater']:02X}    0x{t['pump']:02X}    "
                        f"{t['water_c'] or '?':>5} {t['setpoint_c'] or '?':>3}  {t['frame_num']:>6}  "
                        f"{heater_label(t['heater'])}\n")
            f.write(f"\nByte 14 values seen: {', '.join(f'0x{v:02X}' for v in sorted(seen))}\n")
        print(f"Summary saved to: {summary_path}")

        # Print analysis hint
        print(f"\n💡 To analyze all byte changes between transitions:")
        print(f"   python3 tools/analyze_heating_frames.py {out_path.name}")


if __name__ == "__main__":
    main()

