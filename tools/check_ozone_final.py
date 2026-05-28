"""Final check: compare all non-time bytes between Auto and Manual ozone mode."""
import sys
import importlib.util
sys.path.insert(0, '.')

spec = importlib.util.spec_from_file_location("protocol", "custom_components/joyonway_p25b85/protocol.py")
protocol = importlib.util.module_from_spec(spec)
spec.loader.exec_module(protocol)

find_frames = protocol.find_frames
unescape_frame = protocol.unescape_frame
is_broadcast = protocol.is_broadcast
validate_frame = protocol.validate_frame


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


# The most reliable comparison: "54_auto_observe" was captured RIGHT AFTER
# switching to Auto mode. "57_manual_observe" was captured RIGHT AFTER
# switching to Manual mode. Only a few minutes apart.
# The sequence was: (was manual) -> switch to auto (52-54) -> switch to manual (55-57)

# Better: compare the LAST frame of auto_observe with FIRST frame of manual_baseline
# These are closest in time (manual_baseline is captured right before pressing manual)
auto_observe = get_broadcasts("tools/captures_phase6/54_ozone_mode_auto_observe.bin")
manual_baseline = get_broadcasts("tools/captures_phase6/55_ozone_mode_manual_baseline.bin")

print("Comparing: last frame of 54_auto_observe vs first frame of 55_manual_baseline")
print("(These are adjacent captures — spa is in Auto mode for both)")
print()

if auto_observe and manual_baseline:
    a = auto_observe[-1]
    m = manual_baseline[0]
    # Skip datetime bytes 53-58 and CRC bytes 61-64
    SKIP = set(range(53, 59)) | set(range(61, 65))
    diffs = []
    for i in range(min(len(a), len(m))):
        if i in SKIP:
            continue
        if a[i] != m[i]:
            diffs.append((i, a[i], m[i]))
    if diffs:
        print("Non-time/CRC differences:")
        for i, av, mv in diffs:
            print(f"  byte[{i}]: 0x{av:02X} -> 0x{mv:02X}")
    else:
        print("NO differences outside of clock/CRC bytes!")

print()
print("=" * 60)
print("Now comparing: last frame of 55_manual_baseline vs first of 57_manual_observe")
print("(55=still in Auto, 57=now in Manual — the mode switch happened between these)")
print()

manual_observe = get_broadcasts("tools/captures_phase6/57_ozone_mode_manual_observe.bin")
if manual_baseline and manual_observe:
    # Last frame of baseline (still Auto) vs first frame of observe (now Manual)
    a = manual_baseline[-1]
    m = manual_observe[0]
    SKIP = set(range(53, 59)) | set(range(61, 65))
    diffs = []
    for i in range(min(len(a), len(m))):
        if i in SKIP:
            continue
        if a[i] != m[i]:
            diffs.append((i, a[i], m[i]))
    if diffs:
        print("Non-time/CRC differences (Auto -> Manual):")
        for i, av, mv in diffs:
            print(f"  byte[{i}]: 0x{av:02X} -> 0x{mv:02X}")
    else:
        print("NO differences outside of clock/CRC bytes!")

# Also check: 52_auto_baseline first frame vs 57_manual_observe last frame
# for completeness, but ignore time
print()
print("=" * 60)
print("Full comparison: ALL unique non-time byte values in Auto vs Manual")
print()

auto_all = get_broadcasts("tools/captures_phase6/52_ozone_mode_auto_baseline.bin") + \
           get_broadcasts("tools/captures_phase6/54_ozone_mode_auto_observe.bin")
manual_all = get_broadcasts("tools/captures_phase6/57_ozone_mode_manual_observe.bin") + \
             get_broadcasts("tools/captures_phase6/58_ozone_manual_on_baseline.bin")

SKIP = set(range(53, 59)) | set(range(61, 66))
min_len = min(min(len(f) for f in auto_all), min(len(f) for f in manual_all))

print(f"Auto frames: {len(auto_all)}, Manual frames: {len(manual_all)}")
print()
for i in range(min_len):
    if i in SKIP:
        continue
    auto_vals = set(f[i] for f in auto_all)
    manual_vals = set(f[i] for f in manual_all)
    if auto_vals != manual_vals:
        # Check if they overlap (timing) or are distinct (mode flag)
        overlap = auto_vals & manual_vals
        print(f"  byte[{i:2d}]: Auto={sorted(auto_vals)}, Manual={sorted(manual_vals)}, overlap={sorted(overlap)}")

