"""Check if ozone mode (Auto/Manual) is visible in the broadcast frame."""
import sys
sys.path.insert(0, '.')

from custom_components.joyonway_p25b85.protocol import find_frames, unescape_frame, is_broadcast, validate_frame


def get_broadcasts(filename):
    with open(filename, 'rb') as f:
        raw = f.read()
    frames = find_frames(raw)
    results = []
    for fr in frames:
        if validate_frame(fr) and is_broadcast(fr):
            logical = unescape_frame(fr, full=True)
            results.append(logical)
    return results


# Compare: mode Auto baseline vs mode Manual observe
print("=" * 60)
print("TEST 1: Auto baseline vs Manual observe")
print("=" * 60)
baseline_auto = get_broadcasts('tools/captures_phase6/52_ozone_mode_auto_baseline.bin')
observe_manual = get_broadcasts('tools/captures_phase6/57_ozone_mode_manual_observe.bin')

print(f"Auto baseline frames: {len(baseline_auto)}")
print(f"Manual observe frames: {len(observe_manual)}")

if baseline_auto and observe_manual:
    b = baseline_auto[-1]
    o = observe_manual[-1]
    print(f"Frame lengths: {len(b)} vs {len(o)}")
    print()
    print("Byte differences (auto -> manual):")
    diffs = 0
    for i in range(min(len(b), len(o))):
        if b[i] != o[i]:
            diffs += 1
            print(f"  byte[{i:2d}]: 0x{b[i]:02X} -> 0x{o[i]:02X}")
    if diffs == 0:
        print("  NO DIFFERENCES!")
    print(f"\nTotal diffs: {diffs}")

# Also compare: Manual baseline vs Auto observe (switching back)
print()
print("=" * 60)
print("TEST 2: Manual baseline vs Auto observe")
print("=" * 60)
baseline_manual = get_broadcasts('tools/captures_phase6/55_ozone_mode_manual_baseline.bin')
observe_auto = get_broadcasts('tools/captures_phase6/54_ozone_mode_auto_observe.bin')

print(f"Manual baseline frames: {len(baseline_manual)}")
print(f"Auto observe frames: {len(observe_auto)}")

if baseline_manual and observe_auto:
    b = baseline_manual[-1]
    o = observe_auto[-1]
    print(f"Frame lengths: {len(b)} vs {len(o)}")
    print()
    print("Byte differences (manual -> auto):")
    diffs = 0
    for i in range(min(len(b), len(o))):
        if b[i] != o[i]:
            diffs += 1
            print(f"  byte[{i:2d}]: 0x{b[i]:02X} -> 0x{o[i]:02X}")
    if diffs == 0:
        print("  NO DIFFERENCES!")
    print(f"\nTotal diffs: {diffs}")

# Let's also look at ALL frames to see if there's a consistent byte
print()
print("=" * 60)
print("TEST 3: Check byte consistency across all frames in each file")
print("=" * 60)

for label, frames in [
    ("Auto baseline (52)", baseline_auto),
    ("Manual observe (57)", observe_manual),
    ("Manual baseline (55)", baseline_manual),
    ("Auto observe (54)", observe_auto),
]:
    if not frames:
        continue
    # Show all unique values at each byte position across all frames
    min_len = min(len(f) for f in frames)
    # Find bytes that differ from auto baseline#0
    ref = baseline_auto[0] if baseline_auto else frames[0]
    varying = []
    for i in range(min(min_len, len(ref))):
        vals = set(f[i] for f in frames if len(f) > i)
        if len(vals) > 1:
            varying.append((i, vals))
    print(f"\n{label}: {len(frames)} frames, varying bytes: {[v[0] for v in varying[:10]]}")

