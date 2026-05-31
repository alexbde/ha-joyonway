#!/usr/bin/env python3
"""
Phase 4 analysis — diff baseline vs press captures to isolate command frames.

For each action pair, finds frames that appear in the "press" capture
but NOT in the "baseline" capture (or appear more frequently).
"""
from __future__ import annotations

import os
import sys
from collections import Counter

FRAME_START = 0x1A
FRAME_END = 0x1D

CAPTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "captures_phase4")


def extract_frames(data: bytes) -> list[bytes]:
    """Extract all frames from raw data."""
    frames = []
    i = 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
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


def classify_frame(frame: bytes) -> str:
    """Classify a frame by its destination byte."""
    if len(frame) < 2:
        return "tiny"
    dest = frame[1]
    if dest == 0xFF:
        return "broadcast"
    return f"addr_0x{dest:02x}"


def analyze_pair(baseline_path: str, press_path: str, action: str):
    """Compare baseline and press captures, show unique/extra frames in press."""
    with open(baseline_path, "rb") as f:
        baseline_data = f.read()
    with open(press_path, "rb") as f:
        press_data = f.read()

    baseline_frames = extract_frames(baseline_data)
    press_frames = extract_frames(press_data)

    # Count frame occurrences (by hex content)
    baseline_counter = Counter(f.hex() for f in baseline_frames)
    press_counter = Counter(f.hex() for f in press_frames)

    # Find frames in press but not in baseline (or more frequent)
    new_frames = {}  # hex -> extra count
    for hex_frame, count in press_counter.items():
        baseline_count = baseline_counter.get(hex_frame, 0)
        if count > baseline_count:
            new_frames[hex_frame] = count - baseline_count

    # Also find frames that differ between press and baseline
    # Group non-broadcast frames by length and address
    baseline_non_bc = [f for f in baseline_frames if len(f) > 1 and f[1] != 0xFF]
    press_non_bc = [f for f in press_frames if len(f) > 1 and f[1] != 0xFF]

    # Unique frame types (by hex)
    baseline_types = set(f.hex() for f in baseline_non_bc)
    press_types = set(f.hex() for f in press_non_bc)

    only_in_press = press_types - baseline_types
    only_in_baseline = baseline_types - press_types

    print(f"\n{'═' * 70}")
    print(f"  {action}")
    print(f"{'═' * 70}")
    print(f"  Baseline: {len(baseline_frames)} frames "
          f"({sum(1 for f in baseline_frames if len(f)>1 and f[1]==0xFF)} broadcast, "
          f"{len(baseline_non_bc)} non-broadcast)")
    print(f"  Press:    {len(press_frames)} frames "
          f"({sum(1 for f in press_frames if len(f)>1 and f[1]==0xFF)} broadcast, "
          f"{len(press_non_bc)} non-broadcast)")
    print(f"  Unique frame types — baseline: {len(baseline_types)}, press: {len(press_types)}")

    if only_in_press:
        print(f"\n  🎯 FRAMES ONLY IN PRESS ({len(only_in_press)}):")
        for hex_frame in sorted(only_in_press, key=lambda x: len(x)):
            frame_bytes = bytes.fromhex(hex_frame)
            addr = f"0x{frame_bytes[1]:02x}" if len(frame_bytes) > 1 else "?"
            print(f"     [{len(frame_bytes):2d} bytes] addr={addr}  {hex_frame}")
    else:
        print(f"\n  ⚠️  No completely unique frames in press capture.")

    if new_frames:
        # Show frames that appear MORE in press than baseline
        extra_only = {k: v for k, v in new_frames.items()
                      if k not in only_in_press}
        if extra_only:
            print(f"\n  📊 FRAMES MORE FREQUENT IN PRESS (not unique but extra occurrences):")
            for hex_frame, extra in sorted(extra_only.items(), key=lambda x: -x[1])[:10]:
                frame_bytes = bytes.fromhex(hex_frame)
                addr = f"0x{frame_bytes[1]:02x}" if len(frame_bytes) > 1 else "?"
                bc = press_counter[hex_frame]
                bl = baseline_counter[hex_frame]
                print(f"     [{len(frame_bytes):2d} bytes] addr={addr}  "
                      f"baseline={bl} press={bc} (+{extra})  {hex_frame}")

    if only_in_baseline:
        print(f"\n  ℹ️  Frames only in baseline ({len(only_in_baseline)}) — state change evidence:")
        for hex_frame in sorted(only_in_baseline, key=lambda x: len(x))[:5]:
            frame_bytes = bytes.fromhex(hex_frame)
            addr = f"0x{frame_bytes[1]:02x}" if len(frame_bytes) > 1 else "?"
            print(f"     [{len(frame_bytes):2d} bytes] addr={addr}  {hex_frame}")
        if len(only_in_baseline) > 5:
            print(f"     ... and {len(only_in_baseline) - 5} more")

    # Show broadcast frame differences (state changes visible in broadcasts)
    baseline_bc = [f for f in baseline_frames if len(f) > 1 and f[1] == 0xFF]
    press_bc = [f for f in press_frames if len(f) > 1 and f[1] == 0xFF]
    bc_baseline_types = set(f.hex() for f in baseline_bc)
    bc_press_types = set(f.hex() for f in press_bc)
    bc_only_press = bc_press_types - bc_baseline_types
    if bc_only_press:
        print(f"\n  📡 NEW BROADCAST frames in press (state changed):")
        for hex_frame in sorted(bc_only_press, key=lambda x: len(x))[:3]:
            print(f"     [{len(bytes.fromhex(hex_frame)):2d} bytes] {hex_frame}")

    return only_in_press, new_frames


def main():
    pairs = [
        ("00_cmd_pump_on_baseline.bin", "01_cmd_pump_on_press.bin", "PUMP ON (OFF→low)"),
        ("02_cmd_pump_high_baseline.bin", "03_cmd_pump_high_press.bin", "PUMP HIGH (low→high)"),
        ("04_cmd_pump_off_baseline.bin", "05_cmd_pump_off_press.bin", "PUMP OFF (high→OFF)"),
        ("06_cmd_light_on_baseline.bin", "07_cmd_light_on_press.bin", "LIGHT ON"),
        ("08_cmd_light_off_baseline.bin", "09_cmd_light_off_press.bin", "LIGHT OFF"),
        ("10_cmd_temp_up_baseline.bin", "11_cmd_temp_up_press.bin", "TEMP UP"),
        ("12_cmd_temp_down_baseline.bin", "13_cmd_temp_down_press.bin", "TEMP DOWN"),
    ]

    print("Phase 4 — Command Frame Analysis")
    print("=" * 70)

    all_candidates = {}
    for baseline_file, press_file, action in pairs:
        baseline_path = os.path.join(CAPTURES_DIR, baseline_file)
        press_path = os.path.join(CAPTURES_DIR, press_file)

        if not os.path.exists(baseline_path) or not os.path.exists(press_path):
            print(f"\n  ⚠️  Missing files for {action}, skipping")
            continue

        unique, extra = analyze_pair(baseline_path, press_path, action)
        all_candidates[action] = unique

    # Summary
    print(f"\n\n{'═' * 70}")
    print("  SUMMARY — Command Frame Candidates")
    print(f"{'═' * 70}")
    for action, candidates in all_candidates.items():
        if candidates:
            print(f"\n  {action}: {len(candidates)} unique frame(s)")
            for hex_frame in sorted(candidates, key=lambda x: len(x)):
                print(f"    → {hex_frame}")
        else:
            print(f"\n  {action}: no unique frames (command may be embedded in poll/response cycle)")

    # Look for patterns across actions
    print(f"\n\n{'═' * 70}")
    print("  CROSS-ACTION PATTERN ANALYSIS")
    print(f"{'═' * 70}")

    # Collect all unique command candidates
    all_unique = set()
    for candidates in all_candidates.values():
        all_unique.update(candidates)

    if all_unique:
        # Group by address byte
        by_addr: dict[int, list[str]] = {}
        for hex_frame in all_unique:
            frame_bytes = bytes.fromhex(hex_frame)
            if len(frame_bytes) > 1:
                addr = frame_bytes[1]
                by_addr.setdefault(addr, []).append(hex_frame)

        for addr in sorted(by_addr):
            frames = by_addr[addr]
            print(f"\n  Address 0x{addr:02x} — {len(frames)} unique command frame(s):")
            for hex_frame in sorted(frames):
                # Which actions use this frame?
                actions_using = [a for a, c in all_candidates.items() if hex_frame in c]
                print(f"    {hex_frame}")
                print(f"      Used by: {', '.join(actions_using)}")

        # Try to identify command byte position
        print(f"\n\n  Byte-by-byte comparison of command candidates:")
        if len(all_unique) >= 2:
            sorted_frames = sorted(all_unique, key=lambda x: len(x))
            # Group by length
            by_len: dict[int, list[str]] = {}
            for h in sorted_frames:
                by_len.setdefault(len(h) // 2, []).append(h)
            for length, frames in sorted(by_len.items()):
                if len(frames) >= 2:
                    print(f"\n    Frames of length {length} bytes:")
                    for h in frames:
                        print(f"      {h}")
                    # Find differing positions
                    ref = bytes.fromhex(frames[0])
                    for h in frames[1:]:
                        other = bytes.fromhex(h)
                        diffs = [i for i in range(min(len(ref), len(other))) if ref[i] != other[i]]
                        if diffs:
                            print(f"      Differs at byte(s): {diffs}")
    else:
        print("\n  No unique command frames found across any action.")
        print("  The panel likely embeds commands within the normal poll/response cycle.")
        print("  Need deeper analysis of frame content differences.")


if __name__ == "__main__":
    main()

