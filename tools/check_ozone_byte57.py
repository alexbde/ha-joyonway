"""Deep analysis of byte 57 for ozone mode detection."""
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


# Check byte 57 across all ozone captures
files = [
    ("52 - Auto baseline", "tools/captures_phase6/52_ozone_mode_auto_baseline.bin"),
    ("53 - Auto press", "tools/captures_phase6/53_ozone_mode_auto_press.bin"),
    ("54 - Auto observe", "tools/captures_phase6/54_ozone_mode_auto_observe.bin"),
    ("55 - Manual baseline", "tools/captures_phase6/55_ozone_mode_manual_baseline.bin"),
    ("56 - Manual press", "tools/captures_phase6/56_ozone_mode_manual_press.bin"),
    ("57 - Manual observe", "tools/captures_phase6/57_ozone_mode_manual_observe.bin"),
    ("58 - Manual ON baseline", "tools/captures_phase6/58_ozone_manual_on_baseline.bin"),
    ("59 - Manual ON press", "tools/captures_phase6/59_ozone_manual_on_press.bin"),
    ("60 - Manual ON observe", "tools/captures_phase6/60_ozone_manual_on_observe.bin"),
    ("61 - Manual OFF baseline", "tools/captures_phase6/61_ozone_manual_off_baseline.bin"),
    ("62 - Manual OFF press", "tools/captures_phase6/62_ozone_manual_off_press.bin"),
    ("63 - Manual OFF observe", "tools/captures_phase6/63_ozone_manual_off_observe.bin"),
]

print("Byte 57 values across ozone captures:")
print("-" * 60)
for label, path in files:
    try:
        frames = get_broadcasts(path)
        vals = set()
        for f in frames:
            if len(f) > 57:
                vals.add(f[57])
        vals_str = ", ".join(f"0x{v:02X}" for v in sorted(vals))
        print(f"  {label:30s}: byte[57] = {vals_str}")
    except FileNotFoundError:
        print(f"  {label:30s}: FILE NOT FOUND")

# Also check byte 37-52 range (unexplored area between schedules and datetime)
print()
print("Bytes 37-56 comparison: Auto vs Manual (last frame of each)")
print("-" * 60)

auto_frames = get_broadcasts("tools/captures_phase6/52_ozone_mode_auto_baseline.bin")
manual_frames = get_broadcasts("tools/captures_phase6/57_ozone_mode_manual_observe.bin")

if auto_frames and manual_frames:
    a = auto_frames[-1]
    m = manual_frames[-1]
    print(f"{'Byte':>6} {'Auto':>6} {'Manual':>6} {'Diff':>6}")
    for i in range(37, min(60, len(a), len(m))):
        diff = "  ***" if a[i] != m[i] else ""
        print(f"  [{i:2d}]  0x{a[i]:02X}   0x{m[i]:02X}  {diff}")

