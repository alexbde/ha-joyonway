#!/usr/bin/env python3
"""Guided capture: Date-set commands from PB554 panel.

Captures RS485 bus traffic while the user changes the date on the PB554 panel.
Goal: discover the exact command format for writing dates to the controller.

Usage:
    source .venv/bin/activate
    python tools/capture_date_set.py
"""
from __future__ import annotations

import os
import select
import socket
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HOST = os.environ.get("SPA_BRIDGE_HOST", "192.168.1.100")
PORT = int(os.environ.get("SPA_BRIDGE_PORT", "8899"))

# Protocol constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B
ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

# ANSI
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

CAPTURE_DIR = Path(__file__).resolve().parent / "captures_date"
CAPTURE_DIR.mkdir(exist_ok=True)


def pseudo_unescape(data: bytes) -> bytes:
    result = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if data[i] == ESCAPE_BYTE and i + 1 < n and data[i + 1] in ESCAPE_MAP:
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


def is_command_frame(frame: bytes) -> bool:
    """Non-broadcast frame (likely panel → controller command)."""
    return len(frame) > 2 and frame[1] != 0xFF


def get_broadcast_datetime(data: bytes) -> str | None:
    """Extract spa_datetime from the last broadcast frame in data."""
    for frame in reversed(find_frames(data)):
        if len(frame) > 1 and frame[1] == 0xFF:
            unesc = frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
            if len(unesc) > 58:
                dt_bytes = unesc[53:59]
                try:
                    return (f"{2000 + dt_bytes[0]:04d}-{dt_bytes[1]:02d}-{dt_bytes[2]:02d} "
                            f"{dt_bytes[3]:02d}:{dt_bytes[4]:02d}:{dt_bytes[5]:02d} "
                            f"[raw: {dt_bytes.hex()}]")
                except (ValueError, IndexError):
                    pass
    return None


def capture_until_enter(host: str, port: int) -> bytes:
    """Capture TCP data until user presses Enter. Shows live timer."""
    sock = socket.create_connection((host, port), timeout=10.0)
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

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    try:
        while True:
            elapsed = time.time() - start_time
            print(f"\r  ⏱  Recording... {elapsed:.0f}s (press Enter to stop)", end="", flush=True)
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                sys.stdin.readline()
                break
    except (KeyboardInterrupt, EOFError):
        pass

    stop_event.set()
    t.join(timeout=2.0)
    sock.close()
    duration = time.time() - start_time
    print(f"\r  ⏱  Stopped after {duration:.1f}s" + " " * 30)
    return bytes(buf)


def analyze_commands(data: bytes, label: str) -> list[bytes]:
    """Extract, deduplicate, and display command frames."""
    frames = find_frames(data)
    commands = [f for f in frames if is_command_frame(f)]

    # Deduplicate
    seen = set()
    unique = []
    for cmd in commands:
        if cmd not in seen:
            seen.add(cmd)
            unique.append(cmd)

    print(f"\n  {BOLD}Commands captured ({label}):{RESET}")
    print(f"  Total frames: {len(frames)} ({len(frames) - len(commands)} broadcasts, "
          f"{len(commands)} commands, {len(unique)} unique)")

    if not unique:
        print(f"  {YELLOW}No command frames found!{RESET}")
        return unique

    for i, cmd in enumerate(unique, 1):
        # Unescape inner content for analysis
        inner_unesc = pseudo_unescape(cmd[1:-1])
        print(f"\n  {GREEN}Command {i}:{RESET}")
        print(f"    Wire hex:      {cmd.hex()}")
        print(f"    Unescaped:     {inner_unesc.hex()}")
        print(f"    Length (wire):  {len(cmd)} bytes")
        print(f"    Length (unesc): {len(inner_unesc)} bytes")

        # Try to identify command type
        if len(inner_unesc) >= 5:
            cmd_type = inner_unesc[3]  # byte 4 in 0-indexed unescaped (after removing 0x1A start)
            # Actually byte indices: frame[0]=0x1A, inner starts at frame[1]
            # inner_unesc[0]=0x01, [1]=0x20, [2]=0x10, [3]=0x3C, [4]=cmd_type
            if len(inner_unesc) >= 5:
                print(f"    Byte 4 (type): 0x{inner_unesc[3]:02X}")
            if len(inner_unesc) >= 8:
                print(f"    Bytes 0-7:     {' '.join(f'{b:02x}' for b in inner_unesc[:8])}")
            if len(inner_unesc) >= 16:
                print(f"    Bytes 8-15:    {' '.join(f'{b:02x}' for b in inner_unesc[8:16])}")
                # If this looks like a DateTime command (byte 4 = 0xA2)
                if len(inner_unesc) >= 14 and inner_unesc[3] == 0x3C and inner_unesc[4] == 0xA2:
                    prefix = inner_unesc[7]
                    yr = inner_unesc[8]
                    mo = inner_unesc[9]
                    dy = inner_unesc[10]
                    hr = inner_unesc[11]
                    mn = inner_unesc[12]
                    sc = inner_unesc[13]
                    pad1 = inner_unesc[14] if len(inner_unesc) > 14 else 0
                    pad2 = inner_unesc[15] if len(inner_unesc) > 15 else 0
                    print(f"    {CYAN}→ DateTime cmd! prefix=0x{prefix:02X}{RESET}")
                    print(f"      Year: 0x{yr:02X} = {yr} (→ {2000+yr})")
                    print(f"      Month: 0x{mo:02X} = {mo}")
                    print(f"      Day: 0x{dy:02X} = {dy}")
                    print(f"      Hour: 0x{hr:02X} = {hr}")
                    print(f"      Minute: 0x{mn:02X} = {mn}")
                    print(f"      Second: 0x{sc:02X} = {sc}")
                    print(f"      Pad bytes: 0x{pad1:02X} 0x{pad2:02X}")
                    print(f"      Decoded: {2000+yr}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sc:02d}")

    return unique


def main():
    print(f"\n{'='*60}")
    print(f"  {BOLD}CAPTURE: Date-Set Commands from PB554 Panel{RESET}")
    print(f"{'='*60}")
    print(f"  Bridge: {HOST}:{PORT}")
    print(f"  Output: {CAPTURE_DIR}/")
    print()
    print(f"  {BOLD}Goal:{RESET} Capture what the PB554 sends when you change the DATE")
    print(f"  on the spa controller, so we can replicate it via RS485.")
    print()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ─── Step 1: Baseline ───
    print(f"{'─'*60}")
    print(f"  {BOLD}STEP 1: Baseline (don't touch anything){RESET}")
    print(f"  Recording 5 seconds of idle traffic...")

    sock = socket.create_connection((HOST, PORT), timeout=10.0)
    sock.settimeout(1.0)
    baseline = bytearray()
    deadline = time.time() + 5.0
    try:
        while time.time() < deadline:
            try:
                chunk = sock.recv(4096)
                if chunk:
                    baseline.extend(chunk)
            except socket.timeout:
                continue
    finally:
        sock.close()

    baseline = bytes(baseline)
    spa_dt = get_broadcast_datetime(baseline)
    print(f"  Captured {len(baseline)} bytes")
    if spa_dt:
        print(f"  Current spa clock: {spa_dt}")
    print()

    # ─── Step 2: Navigate to date setting ───
    print(f"{'─'*60}")
    print(f"  {BOLD}STEP 2: Navigate to the date/time setting screen{RESET}")
    print(f"""
  On the PB554 panel:
  1. Go to Settings / Clock / Date-Time menu
  2. Navigate to the DATE field (year, month, or day)
  3. DO NOT change anything yet — just get ready
  
  {YELLOW}Note what date is currently displayed on the panel!{RESET}
""")
    input(f"  {BOLD}>>> Press Enter when ready to capture the date change...{RESET} ")

    # ─── Step 3: Capture date change ───
    print(f"\n{'─'*60}")
    print(f"  {BOLD}STEP 3: Change the DATE on the panel NOW{RESET}")
    print(f"""
  While recording, change the date on the PB554:
  - Change the DAY (e.g., from 27 to 28, or +1/-1)
  - Confirm/save the change on the panel
  
  Press Enter here AFTER you've confirmed the change on the panel.
""")

    data_date = capture_until_enter(HOST, PORT)

    # Save raw capture
    outfile = CAPTURE_DIR / f"date_change_{ts}.bin"
    outfile.write_bytes(data_date)
    print(f"  Saved: {outfile.name} ({len(data_date)} bytes)")

    # Analyze
    spa_dt_after = get_broadcast_datetime(data_date)
    if spa_dt_after:
        print(f"  Spa clock after: {spa_dt_after}")

    cmds = analyze_commands(data_date, "date change")

    # ─── Step 4: Optional — capture time-only change for comparison ───
    print(f"\n{'─'*60}")
    print(f"  {BOLD}STEP 4 (optional): Change only the TIME for comparison{RESET}")
    resp = input(f"  {BOLD}>>> Also capture a time-only change? [y/N]: {RESET}").strip().lower()

    if resp in ("y", "yes"):
        print(f"\n  Change only the HOUR or MINUTE on the panel (not the date).")
        print(f"  Press Enter here AFTER you've confirmed.")

        data_time = capture_until_enter(HOST, PORT)

        outfile2 = CAPTURE_DIR / f"time_change_{ts}.bin"
        outfile2.write_bytes(data_time)
        print(f"  Saved: {outfile2.name} ({len(data_time)} bytes)")

        cmds_time = analyze_commands(data_time, "time-only change")

        # Compare
        if cmds and cmds_time:
            print(f"\n  {BOLD}COMPARISON:{RESET}")
            print(f"  Date-change commands: {len(cmds)} unique")
            print(f"  Time-change commands: {len(cmds_time)} unique")

            # Check if same structure
            if cmds[0][:6] == cmds_time[0][:6]:
                print(f"  {GREEN}Same header structure — likely same command type{RESET}")
            else:
                print(f"  {YELLOW}Different header! Date may use a different command{RESET}")
                print(f"    Date cmd starts: {cmds[0][:8].hex()}")
                print(f"    Time cmd starts: {cmds_time[0][:8].hex()}")

    # ─── Step 5: Optional — change date again (different value) ───
    print(f"\n{'─'*60}")
    print(f"  {BOLD}STEP 5 (optional): Change date to a DIFFERENT value{RESET}")
    print(f"  This gives us two date-change captures to compare.")
    resp = input(f"  {BOLD}>>> Capture another date change? [y/N]: {RESET}").strip().lower()

    if resp in ("y", "yes"):
        print(f"\n  Change the date to something else (e.g., different day or month).")
        print(f"  Press Enter here AFTER you've confirmed.")

        data_date2 = capture_until_enter(HOST, PORT)

        outfile3 = CAPTURE_DIR / f"date_change2_{ts}.bin"
        outfile3.write_bytes(data_date2)
        print(f"  Saved: {outfile3.name} ({len(data_date2)} bytes)")

        analyze_commands(data_date2, "second date change")

    # ─── Summary ───
    print(f"\n{'='*60}")
    print(f"  {BOLD}DONE!{RESET}")
    print(f"{'='*60}")
    print(f"\n  Captures saved in: {CAPTURE_DIR}/")
    print(f"  Files: {', '.join(f.name for f in sorted(CAPTURE_DIR.glob(f'*{ts}*')))}")
    print(f"""
  {BOLD}Next steps:{RESET}
  - Look at the DateTime commands captured above
  - Compare the date-change command with the time-change command
  - Check if byte 7 (prefix), byte encoding, or command type differs
  - If the commands look identical to what we already send (0xA2, prefix=0x50),
    then the panel might be doing something ELSE (like a multi-step sequence)
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    except ConnectionRefusedError:
        print(f"\n  {RED}ERROR: Cannot connect to {HOST}:{PORT}{RESET}")
        print(f"  Make sure the EW11 bridge is powered on and reachable.")

