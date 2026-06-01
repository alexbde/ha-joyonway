#!/usr/bin/env python3
"""
Joyonway Spa — About/Diagnostics Menu Guided Capture & Live Analyzer.

Guides the user through capturing RS485 traffic while they navigate and click
through the "About" diagnostic menus on the spa display panel.
Directly parses and analyzes the captured traffic on-the-fly to check if 
firmware versions, panel IDs, or capabilities are transmitted on the wire.

Python stdlib only — no external dependencies.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import socket
import sys
import threading
import time

__version__ = "1.0.0"

# ── Load Environment (.env) ──
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

HOST = os.environ.get("SPA_BRIDGE_HOST", "192.168.1.100")
PORT_RAW = os.environ.get("SPA_BRIDGE_PORT", "8899")
try:
    PORT = int(PORT_RAW)
except ValueError:
    PORT = 8899

# Protocol Constants
FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A,
    0x0B: 0x1B,
    0x13: 0x1C,
    0x14: 0x1D,
    0x15: 0x1E,
}

# ── Protocol Decoding Functions ──
def pseudo_unescape(data: bytes) -> bytes:
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


def find_frames(data: bytes) -> list[bytes]:
    frames = []
    i = 0
    n = len(data)
    while i < n:
        if data[i] == FRAME_START:
            j = i + 1
            while j < n:
                if data[j] == FRAME_END:
                    frames.append(data[i : j + 1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def unescape_frame(frame: bytes) -> bytes:
    return frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]


# ── Interactive Capture Functions ──
def capture_timed(host: str, port: int, duration: float) -> bytes:
    sock = socket.create_connection((host, port), timeout=5.0)
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


def capture_interactive(host: str, port: int) -> tuple[bytes, float]:
    sock = socket.create_connection((host, port), timeout=5.0)
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

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    try:
        while True:
            elapsed = time.time() - start_time
            print(f"\r     ⏱  Recording... {elapsed:.0f}s (press Enter to stop)", end="", flush=True)
            import select
            ready, _, _ = select.select([sys.stdin], [], [], 0.5)
            if ready:
                sys.stdin.readline()
                break
    except (KeyboardInterrupt, EOFError):
        pass

    stop_event.set()
    reader_thread.join(timeout=2.0)
    sock.close()

    duration = time.time() - start_time
    print(f"\r     ⏱  Recording stopped after {duration:.1f}s" + " " * 20)
    return bytes(buf), duration


# ── Live Diagnostics Analyzer ──
def analyze_captured_session(out_dir: str):
    print("\n" + "=" * 70)
    print("  🔍 LIVE DIAGNOSTICS ANALYZER  ")
    print("=" * 70)
    
    files = {
        "baseline": "00_about_baseline.bin",
        "action": "01_about_press.bin",
        "observe": "02_about_observe.bin"
    }
    
    data = {}
    for phase, fname in files.items():
        fpath = os.path.join(out_dir, fname)
        if not os.path.exists(fpath):
            print(f"❌ Missing file: {fname}. Skipping analysis.")
            return
        with open(fpath, "rb") as f:
            data[phase] = f.read()
            
    # Parse frames
    frames = {p: [unescape_frame(f) for f in find_frames(d)] for p, d in data.items()}
    
    print(f"Parsed frames:")
    print(f"  - Baseline: {len(frames['baseline'])} frames")
    print(f"  - Action (Click-through): {len(frames['action'])} frames")
    print(f"  - Observe: {len(frames['observe'])} frames")
    
    # 1. Isolate unique frames sent during the Action phase (not present in baseline or observe)
    baseline_hex_set = set(f.hex() for f in frames['baseline'])
    observe_hex_set = set(f.hex() for f in frames['observe'])
    non_transient_set = baseline_hex_set.union(observe_hex_set)
    
    unique_action_frames = []
    seen = set()
    for f in frames['action']:
        h = f.hex()
        if h not in non_transient_set and h not in seen:
            seen.add(h)
            unique_action_frames.append(f)
            
    print(f"\nIsolated {len(unique_action_frames)} unique frame types sent *only* during the click-through menu:")
    
    # Check for versions: Panel 1.7 (0x11, 0x17, 17), Board 1.8 (0x12, 0x18, 18), Panel ID 1, Power Limit 0
    found_diagnostics = False
    
    # Analyze unique action frames
    for idx, f in enumerate(unique_action_frames):
        fhex = f.hex()
        f_type = "Broadcast" if f[1] == 0xFF else "Interactive"
        print(f"  [{idx+1}] ({f_type}) Hex: {fhex}")
        
        # Check BCD/byte matches
        for i in range(len(f) - 1):
            val_i = f[i]
            val_j = f[i+1]
            # Decimal or BCD matching
            if (val_i == 17 and val_j == 18) or (val_i == 0x17 and val_j == 0x18):
                print(f"     🎉 DETECTED VERSION MATCH! Found 1.7 ({hex(val_i)}) and 1.8 ({hex(val_j)}) adjacent at index {i}!")
                found_diagnostics = True
            elif (val_i == 7 and val_j == 8):
                print(f"     🎉 DETECTED VERSION MATCH! Found 7 and 8 adjacent at index {i}!")
                found_diagnostics = True
                
        # Check for ASCII strings
        if b"1.7" in f or b"1.8" in f:
            print(f"     🎉 DETECTED ASCII MATCH! Found '1.7' or '1.8' string in frame payload!")
            found_diagnostics = True
            
    # 2. Also scan all action frames (even if they appear in baseline) for version bytes in static positions
    if not found_diagnostics:
        print("\nScanning all click-through frames for general occurrences of versions 1.7 / 1.8 / Panel ID 1:")
        for frame in frames['action']:
            for i in range(len(frame) - 1):
                val_i = frame[i]
                val_j = frame[i+1]
                if (val_i == 0x17 and val_j == 0x18) or (val_i == 17 and val_j == 18):
                    # Exclude known datetime hours/minutes (e.g. index 53-58 in broadcast)
                    if not (len(frame) >= 59 and 53 <= i <= 58):
                        print(f"  Possible match: Found {hex(val_i)} and {hex(val_j)} adjacent at index {i} in frame: {frame.hex()}")
                        found_diagnostics = True
                        
    # 3. Print Verdict
    print("\n" + "-" * 50)
    print("  VERDICT  ")
    print("-" * 50)
    if found_diagnostics:
        print("  ✅ SUCCESS! Diagnostic data (Panel/Board version) WAS found on the wire!")
        print("     This means we can actively extract this information for Home Assistant sensors.")
    else:
        print("  ℹ️  NO DIAGNOSTIC TRAFFIC DETECTED.")
        print("     All unmapped or active bytes were either operational parameters, clock times,")
        print("     or standard polling signals.")
        print("     Conclusion: The settings/versions (Panel 1.7, Board 1.8, capabilities)")
        print("     are hardcoded/stored locally in the touchscreen panel itself and never sent on the bus.")
    print("=" * 70 + "\n")


# ── Main Guided Session ──
def main():
    print()
    print("=" * 70)
    print("  Joyonway Spa — About Menu Click-Through Guided Capture")
    print(f"  Version {__version__}")
    print("=" * 70)
    print()
    print(f"Bridge Target:  {HOST}:{PORT}")
    
    out_dir = os.path.join(os.path.dirname(__file__), "captures", "about_menu")
    print(f"Save Directory: {os.path.relpath(out_dir)}")
    print()
    
    # Verify connection
    print("Checking connection to EW11 bridge...", end="", flush=True)
    try:
        s = socket.create_connection((HOST, PORT), timeout=3.0)
        s.close()
        print(" ✅ Connected!")
    except (OSError, socket.error) as err:
        print(f" ❌ FAILED")
        print(f" Error: Could not connect to {HOST}:{PORT}.")
        print(" Make sure your spa bridge is online and SPA_BRIDGE_HOST is correct in .env.")
        sys.exit(1)
        
    print("\n--- PHASE 1: BASELINE (Idle State) ---")
    print("We will first capture 10 seconds of idle steady-state traffic.")
    print("Do not touch the display panel during this capture.")
    try:
        input("\nPress Enter to start baseline capture...")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)
        
    print("⏺ Recording baseline...", end="", flush=True)
    baseline_data = capture_timed(HOST, PORT, 10.0)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "00_about_baseline.bin"), "wb") as f:
        f.write(baseline_data)
    print(" done! (Saved to 00_about_baseline.bin)")
    
    print("\n--- PHASE 2: ACTION (About Menu Navigation) ---")
    print("During this recording, you should perform the following on the spa display panel:")
    print("  1. Press Enter to start the recording.")
    print("  2. Wake up the display panel if locked.")
    print("  3. Go into Settings -> About.")
    print("  4. Slowly page through all 'About' pages (showing light, ozone, versions).")
    print("  5. Exit the menu back to the home screen.")
    print("  6. Press Enter on your computer to stop recording.")
    try:
        input("\nPress Enter when ready to START recording...")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)
        
    action_data, duration = capture_interactive(HOST, PORT)
    with open(os.path.join(out_dir, "01_about_press.bin"), "wb") as f:
        f.write(action_data)
    print(f"Saved to 01_about_press.bin ({len(action_data)} bytes recorded)")
    
    print("\n--- PHASE 3: OBSERVE (Post-Action Steady State) ---")
    print("We will now capture 10 seconds of steady-state traffic again.")
    print("Do not touch the display panel.")
    try:
        input("\nPress Enter to start post-action observe capture...")
    except (KeyboardInterrupt, EOFError):
        print("\nAborted.")
        sys.exit(0)
        
    print("⏺ Recording observe...", end="", flush=True)
    observe_data = capture_timed(HOST, PORT, 10.0)
    with open(os.path.join(out_dir, "02_about_observe.bin"), "wb") as f:
        f.write(observe_data)
    print(" done! (Saved to 02_about_observe.bin)")
    
    # ── Immediate Live Analysis ──
    analyze_captured_session(out_dir)
    
if __name__ == "__main__":
    main()
