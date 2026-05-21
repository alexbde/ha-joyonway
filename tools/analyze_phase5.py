#!/usr/bin/env python3
"""Analyze Phase 5 captures — extract command frames and broadcast diffs."""
from __future__ import annotations
import json
import os
import sys

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B
ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

CAPTURE_DIR = os.path.join(os.path.dirname(__file__), "captures_phase5")


def pseudo_unescape(data: bytes) -> bytes:
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == ESCAPE_BYTE and i + 1 < len(data) and data[i + 1] in ESCAPE_MAP:
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


def classify(frame: bytes) -> str:
    if len(frame) > 1 and frame[1] == 0xFF:
        return "broadcast"
    if len(frame) > 2 and frame[1] == 0x01 and frame[2] == 0x20:
        return "panel_cmd"
    if len(frame) > 1 and frame[1] == 0x20:
        return "to_panel"
    return "other"


def analyze_file(path: str):
    with open(path, "rb") as f:
        data = f.read()
    frames = find_frames(data)
    broadcasts = [fr for fr in frames if classify(fr) == "broadcast"]
    panel_cmds = [fr for fr in frames if classify(fr) == "panel_cmd"]
    to_panel = [fr for fr in frames if classify(fr) == "to_panel"]
    others = [fr for fr in frames if classify(fr) == "other"]
    return {
        "frames": frames,
        "broadcasts": broadcasts,
        "panel_cmds": panel_cmds,
        "to_panel": to_panel,
        "others": others,
    }


def broadcast_state(frame: bytes) -> dict:
    unesc = frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
    state = {}
    if len(unesc) > 58:
        for i in range(len(unesc)):
            state[i] = unesc[i]
    return state


def print_broadcast_diff(label: str, baseline_frames: list[bytes], other_frames: list[bytes]):
    if not baseline_frames or not other_frames:
        print(f"  {label}: no broadcasts to compare")
        return

    # Get the LAST broadcast from each to compare settled state
    b_state = broadcast_state(baseline_frames[-1])
    o_state = broadcast_state(other_frames[-1])

    if not b_state or not o_state:
        print(f"  {label}: could not parse broadcasts")
        return

    diffs = []
    for idx in sorted(set(b_state) | set(o_state)):
        bv = b_state.get(idx)
        ov = o_state.get(idx)
        if bv != ov:
            diffs.append((idx, bv, ov))

    if diffs:
        print(f"  {label} — {len(diffs)} byte(s) changed:")
        for idx, bv, ov in diffs:
            bv_str = f"0x{bv:02X}" if bv is not None else "N/A"
            ov_str = f"0x{ov:02X}" if ov is not None else "N/A"
            print(f"    byte[{idx:2d}]: {bv_str} → {ov_str}")
    else:
        print(f"  {label} — no broadcast changes detected")


def main():
    manifest_path = os.path.join(CAPTURE_DIR, "session_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    segments = manifest["segments"]

    # Group segments by action
    actions = {}
    for seg in segments:
        action = seg["action"]
        phase = seg["phase"]
        if action not in actions:
            actions[action] = {}
        actions[action][phase] = seg

    print("=" * 70)
    print("Phase 5 Capture Analysis")
    print("=" * 70)

    for action_name, phases in actions.items():
        print(f"\n{'━' * 70}")
        print(f"ACTION: {action_name}")
        print(f"{'━' * 70}")

        for phase_name in ["baseline", "press", "observe"]:
            if phase_name not in phases:
                continue
            seg = phases[phase_name]
            filepath = os.path.join(CAPTURE_DIR, seg["filename"])
            result = analyze_file(filepath)

            print(f"\n  {phase_name.upper()} ({seg['filename']}):")
            print(f"    Total frames: {len(result['frames'])}")
            print(f"    Broadcasts:   {len(result['broadcasts'])}")
            print(f"    Panel→Ctrl:   {len(result['panel_cmds'])}")
            print(f"    Ctrl→Panel:   {len(result['to_panel'])}")
            print(f"    Other:        {len(result['others'])}")

            if phase_name == "press" and result["panel_cmds"]:
                # Find unique panel commands
                unique_cmds = {}
                for cmd in result["panel_cmds"]:
                    h = cmd.hex()
                    unique_cmds[h] = unique_cmds.get(h, 0) + 1

                print(f"\n    🔍 Unique panel→controller commands ({len(unique_cmds)}):")
                for hexstr, count in sorted(unique_cmds.items(), key=lambda x: -x[1]):
                    print(f"      [{count:3d}x] {hexstr}")
                    # Decode
                    raw = bytes.fromhex(hexstr)
                    unesc = raw[:1] + pseudo_unescape(raw[1:-1]) + raw[-1:]
                    print(f"             unescaped ({len(unesc)} bytes): {unesc.hex()}")
                    if len(unesc) >= 22:
                        print(f"             byte[8:12]={unesc[8]:02x} {unesc[9]:02x} {unesc[10]:02x} {unesc[11]:02x}"
                              f"  byte[15]=0x{unesc[15]:02X}({unesc[15]}°F)")

        # Show broadcast diffs
        if "baseline" in phases and "press" in phases:
            baseline_data = analyze_file(os.path.join(CAPTURE_DIR, phases["baseline"]["filename"]))
            press_data = analyze_file(os.path.join(CAPTURE_DIR, phases["press"]["filename"]))
            print()
            print_broadcast_diff("baseline → press", baseline_data["broadcasts"], press_data["broadcasts"])

        if "baseline" in phases and "observe" in phases:
            baseline_data = analyze_file(os.path.join(CAPTURE_DIR, phases["baseline"]["filename"]))
            observe_data = analyze_file(os.path.join(CAPTURE_DIR, phases["observe"]["filename"]))
            print_broadcast_diff("baseline → observe", baseline_data["broadcasts"], observe_data["broadcasts"])

        # Compare baseline vs press: find NEW commands (in press but not baseline)
        if "baseline" in phases and "press" in phases:
            baseline_data = analyze_file(os.path.join(CAPTURE_DIR, phases["baseline"]["filename"]))
            press_data = analyze_file(os.path.join(CAPTURE_DIR, phases["press"]["filename"]))

            baseline_cmds = set(cmd.hex() for cmd in baseline_data["panel_cmds"])
            press_cmds_unique = {}
            for cmd in press_data["panel_cmds"]:
                h = cmd.hex()
                press_cmds_unique[h] = press_cmds_unique.get(h, 0) + 1

            new_cmds = {h: c for h, c in press_cmds_unique.items() if h not in baseline_cmds}

            if new_cmds:
                print(f"\n  ⚡ NEW commands (in press but NOT in baseline):")
                for hexstr, count in sorted(new_cmds.items(), key=lambda x: -x[1]):
                    print(f"    [{count}x] {hexstr}")
                    raw = bytes.fromhex(hexstr)
                    unesc = raw[:1] + pseudo_unescape(raw[1:-1]) + raw[-1:]
                    print(f"         unescaped ({len(unesc)} bytes): {unesc.hex()}")
            else:
                print(f"\n  ⚠️  No NEW commands in press vs baseline — button press may not have been captured")


if __name__ == "__main__":
    main()

