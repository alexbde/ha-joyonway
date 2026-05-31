#!/usr/bin/env python3
"""Quick probe: connect to spa bridge and parse broadcast frames."""
from __future__ import annotations

import argparse
import os
import socket
import sys
import time

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

DEFAULT_HOST = os.environ.get("SPA_BRIDGE_HOST", "192.168.1.100")
DEFAULT_PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))
DEFAULT_DURATION = 6.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect to RS485 bridge and print decoded broadcast probe data.",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Bridge host (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Bridge port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--duration",
        type=float,
        default=DEFAULT_DURATION,
        help=f"Capture duration in seconds (default: {DEFAULT_DURATION})",
    )
    parser.add_argument("--timeout", type=float, default=10.0, help="Socket connect timeout in seconds")
    return parser.parse_args()


def capture_bytes(host: str, port: int, duration: float, timeout: float) -> bytes:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(8)
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
                break
    finally:
        sock.close()
    return bytes(buf)


def print_probe_report(buf: bytes) -> None:
    print(f"Total: {len(buf)} bytes\n")

    i = 0
    frame_count = 0
    broadcast_count = 0
    while i < len(buf):
        if buf[i] == 0x1A:
            j = buf.find(0x1D, i + 1)
            if j != -1:
                frame = buf[i : j + 1]
                dst = frame[1] if len(frame) > 1 else 0
                frame_count += 1
                if dst == 0xFF and len(frame) > 20:
                    broadcast_count += 1
                    print(f"=== BROADCAST frame (#{frame_count} overall, {len(frame)} bytes) ===")
                    print(f"  hex: {frame.hex(' ')}")
                    if len(frame) > 8:
                        model = "P25B85" if frame[8] == 0x03 else "P23B32" if frame[8] == 0x02 else "unknown"
                        print(f"  byte[8]  = 0x{frame[8]:02X}  (model: {model})")
                    if len(frame) > 9:
                        tf = frame[9]
                        tc = round((tf - 32) * 5 / 9, 1)
                        print(f"  byte[9]  = 0x{tf:02X}  water temp: {tf} F = {tc} C")
                    if len(frame) > 13:
                        print(f"  byte[13] = 0x{frame[13]:02X}  pump status (0x02=filter, 0x04=massage)")
                    if len(frame) > 15:
                        b15 = frame[15]
                        states = {0x00: "off", 0x50: "circ", 0x54: "heating", 0x40: "cooldown", 0xC1: "UV/ozone"}
                        print(f"  byte[15] = 0x{b15:02X}  heating state: {states.get(b15, 'unknown')}")
                    if len(frame) > 16:
                        sf = frame[16]
                        sc = round((sf - 32) * 5 / 9, 1)
                        print(f"  byte[16] = 0x{sf:02X}  setpoint: {sf} F = {sc} C")
                    if len(frame) > 18:
                        print(f"  byte[18] = 0x{frame[18]:02X}  light: {'ON' if frame[18] & 0x01 else 'OFF'}")
                    if len(frame) > 29:
                        print(f"  byte[29] = 0x{frame[29]:02X}  UV flag: {'ACTIVE' if frame[29] & 0x20 else 'off'}")
                    print()
                i = j + 1
                continue
        i += 1

    print(f"Total frames: {frame_count}, broadcast frames: {broadcast_count}")

    sig = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])
    idx = buf.find(sig)
    print(f"\nP23B32 signature (byte8=0x02) found: {idx != -1}")
    sig85 = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])
    idx85 = buf.find(sig85)
    print(f"P25B85 signature (byte8=0x03) found: {idx85 != -1}")


def main() -> int:
    args = parse_args()
    try:
        buf = capture_bytes(args.host, args.port, args.duration, args.timeout)
    except OSError as err:
        print(f"Connection error: {err}", file=sys.stderr)
        return 1

    print_probe_report(buf)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


