"""Analyze a heating cycle JSONL capture to find ALL bytes that change at transitions.

Usage:
    python3 tools/analyze_heating_frames.py <filename.jsonl>

Reads the full-frame capture and reports:
1. All unique byte 14 values seen (with frame ranges)
2. For each transition in byte 14: which OTHER bytes also changed
3. Bytes that are consistently different between heating phases
   (candidates for circulation/status indicators)
"""
import json
import sys
from collections import defaultdict
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        # Try to find the most recent capture
        cap_dir = Path(__file__).resolve().parent / "captures_heating"
        files = sorted(cap_dir.glob("*.jsonl"))
        if not files:
            print("Usage: python3 tools/analyze_heating_frames.py <filename.jsonl>")
            print("No captures found in tools/captures_heating/")
            sys.exit(1)
        filepath = files[-1]
        print(f"Using most recent capture: {filepath.name}\n")
    else:
        arg = sys.argv[1]
        filepath = Path(arg)
        if not filepath.exists():
            filepath = Path(__file__).resolve().parent / "captures_heating" / arg
        if not filepath.exists():
            print(f"File not found: {arg}")
            sys.exit(1)

    # Load all frames
    frames = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                frames.append(json.loads(line))

    if not frames:
        print("No frames in file.")
        sys.exit(1)

    print(f"Loaded {len(frames)} frames, duration {frames[-1]['elapsed_s']:.1f}s\n")

    # ─── Section 1: Byte 14 phases ───────────────────────────────────────
    print("=" * 80)
    print("BYTE 14 (HEATER STATE) PHASES")
    print("=" * 80)

    phases = []
    current_phase = {"value": frames[0]["byte14_heater"], "start_frame": 1, "start_time": frames[0]["time"]}

    for fr in frames[1:]:
        if fr["byte14_heater"] != current_phase["value"]:
            current_phase["end_frame"] = fr["frame"] - 1
            current_phase["end_time"] = frames[fr["frame"] - 2]["time"]
            phases.append(current_phase)
            current_phase = {"value": fr["byte14_heater"], "start_frame": fr["frame"], "start_time": fr["time"]}

    current_phase["end_frame"] = frames[-1]["frame"]
    current_phase["end_time"] = frames[-1]["time"]
    phases.append(current_phase)

    print(f"\n{'Phase':<6} {'Byte14':<8} {'Frames':<14} {'Time range':<30} {'Duration'}")
    print("-" * 75)
    for i, p in enumerate(phases):
        n_frames = p["end_frame"] - p["start_frame"] + 1
        print(f"  {i+1:<4} {p['value']:<8} {p['start_frame']:>4}-{p['end_frame']:<6} "
              f"{p['start_time']} - {p['end_time']:<14} ({n_frames} frames)")

    # ─── Section 2: Transition analysis ──────────────────────────────────
    print(f"\n{'=' * 80}")
    print("TRANSITION ANALYSIS — bytes that change at each byte 14 transition")
    print("=" * 80)

    for i in range(1, len(phases)):
        prev_phase = phases[i - 1]
        curr_phase = phases[i]

        # Get the last frame of prev phase and first frame of current phase
        prev_frame_idx = prev_phase["end_frame"] - 1  # 0-based
        curr_frame_idx = curr_phase["start_frame"] - 1  # 0-based

        if prev_frame_idx >= len(frames) or curr_frame_idx >= len(frames):
            continue

        prev_hex = bytes.fromhex(frames[prev_frame_idx]["hex"])
        curr_hex = bytes.fromhex(frames[curr_frame_idx]["hex"])

        print(f"\n┌─ Transition {i}: {prev_phase['value']} → {curr_phase['value']}")
        print(f"│  Time: {prev_phase['end_time']} → {curr_phase['start_time']}")
        print(f"│  Frames: #{prev_phase['end_frame']} → #{curr_phase['start_frame']}")
        print(f"│")
        print(f"│  Bytes that changed:")

        changes = []
        for b in range(min(len(prev_hex), len(curr_hex))):
            if prev_hex[b] != curr_hex[b]:
                changes.append((b, prev_hex[b], curr_hex[b]))

        if changes:
            for idx, old, new in changes:
                marker = " ◀ BYTE 14" if idx == 14 else ""
                marker = " ◀ BYTE 12 (pump)" if idx == 12 else marker
                print(f"│    Byte {idx:>2}: 0x{old:02X} → 0x{new:02X}  "
                      f"(dec: {old:>3} → {new:>3}){marker}")
        else:
            print(f"│    (none besides byte 14 — check if transition happened between frames)")
        print(f"└─")

    # ─── Section 3: Per-phase byte stability ─────────────────────────────
    print(f"\n{'=' * 80}")
    print("PER-BYTE VARIATION ACROSS PHASES")
    print("Shows bytes that have DIFFERENT stable values in different phases")
    print("(Candidates for circulation/status indicators)")
    print("=" * 80)

    # For each phase, compute the most common value of each byte
    phase_byte_modes = []
    for p in phases:
        start_idx = p["start_frame"] - 1
        end_idx = p["end_frame"]
        phase_frames = frames[start_idx:end_idx]

        byte_counts = defaultdict(lambda: defaultdict(int))
        for fr in phase_frames:
            payload = bytes.fromhex(fr["hex"])
            for b in range(len(payload)):
                byte_counts[b][payload[b]] += 1

        # Mode (most common value) for each byte
        modes = {}
        for b, counts in byte_counts.items():
            modes[b] = max(counts, key=counts.get)
        phase_byte_modes.append(modes)

    # Find bytes where mode differs between phases (excluding known bytes)
    known_changing = {14, 12}  # byte 14 (heater) and 12 (pump) already known
    # Also exclude temperature bytes that naturally drift
    temp_bytes = set()
    # Detect which byte indices are used for water temp / setpoint from the first frame
    first_payload = bytes.fromhex(frames[0]["hex"])

    interesting_bytes = []
    max_byte = min(len(m) for m in phase_byte_modes) if phase_byte_modes else 0

    for b in range(max_byte):
        values_across_phases = [m.get(b) for m in phase_byte_modes if b in m]
        if len(set(values_across_phases)) > 1 and b not in known_changing:
            interesting_bytes.append(b)

    if interesting_bytes:
        print(f"\n{'Byte':<6}", end="")
        for i, p in enumerate(phases):
            print(f"  {'Phase ' + str(i+1) + ' (' + p['value'] + ')':<20}", end="")
        print()
        print("-" * (6 + 22 * len(phases)))

        for b in interesting_bytes:
            print(f"  {b:<4}", end="")
            for modes in phase_byte_modes:
                val = modes.get(b)
                if val is not None:
                    print(f"  0x{val:02X} ({val:>3})          ", end="")
                else:
                    print(f"  {'?':<20}", end="")
            print()
    else:
        print("\n  No additional bytes found with different stable values between phases.")
        print("  (The circulation indicator may be a brief transient or in byte 14 itself)")

    # ─── Section 4: Frame-by-frame changes around transitions ────────────
    print(f"\n{'=' * 80}")
    print("FRAME-BY-FRAME DETAIL AROUND TRANSITIONS (±3 frames)")
    print("=" * 80)

    for i in range(1, len(phases)):
        transition_frame = phases[i]["start_frame"]
        start = max(0, transition_frame - 4)  # 3 before
        end = min(len(frames), transition_frame + 3)  # 3 after

        print(f"\n─── Transition {i}: frame #{transition_frame} "
              f"({phases[i-1]['value']} → {phases[i]['value']}) ───")
        print(f"{'Frame':<7} {'Time':<14} {'Byte14':<8} {'Changed bytes'}")
        print("-" * 70)

        for idx in range(start, end):
            fr = frames[idx]
            marker = " <<<" if fr["frame"] == transition_frame else ""
            changed_str = ""
            if "changed" in fr:
                parts = []
                for byte_idx, change in fr["changed"].items():
                    parts.append(f"b{byte_idx}:{change}")
                changed_str = " ".join(parts)
            print(f"  {fr['frame']:<5} {fr['time']:<14} {fr['byte14_heater']:<8} {changed_str}{marker}")

    print(f"\n{'=' * 80}")
    print("DONE. Look for bytes that change specifically at the heating→off transition")
    print("that could indicate a post-heat circulation phase.")
    print("=" * 80)


if __name__ == "__main__":
    main()

