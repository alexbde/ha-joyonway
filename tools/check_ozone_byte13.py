"""Verify byte 13 bit 7 as ozone mode flag."""
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


files = [
    ("52 - Auto baseline (spa was just switched TO auto)", "tools/captures_phase6/52_ozone_mode_auto_baseline.bin"),
    ("53 - Auto press (pressing auto again?)", "tools/captures_phase6/53_ozone_mode_auto_press.bin"),
    ("54 - Auto observe (just confirmed auto)", "tools/captures_phase6/54_ozone_mode_auto_observe.bin"),
    ("55 - Manual baseline (still auto, about to press manual)", "tools/captures_phase6/55_ozone_mode_manual_baseline.bin"),
    ("56 - Manual press (pressing manual)", "tools/captures_phase6/56_ozone_mode_manual_press.bin"),
    ("57 - Manual observe (now in manual)", "tools/captures_phase6/57_ozone_mode_manual_observe.bin"),
    ("58 - Manual ON baseline (in manual, about to turn ON)", "tools/captures_phase6/58_ozone_manual_on_baseline.bin"),
    ("59 - Manual ON press", "tools/captures_phase6/59_ozone_manual_on_press.bin"),
    ("60 - Manual ON observe (ozone running)", "tools/captures_phase6/60_ozone_manual_on_observe.bin"),
    ("61 - Manual OFF baseline (ozone running)", "tools/captures_phase6/61_ozone_manual_off_baseline.bin"),
    ("62 - Manual OFF press", "tools/captures_phase6/62_ozone_manual_off_press.bin"),
    ("63 - Manual OFF observe (ozone stopped)", "tools/captures_phase6/63_ozone_manual_off_observe.bin"),
]

print(f"{'File':<55} {'byte[13] values':<30} {'bit7 (0x80)'}")
print("-" * 100)
for label, path in files:
    try:
        frames = get_broadcasts(path)
        vals = [f[13] for f in frames if len(f) > 13]
        unique = sorted(set(vals))
        bit7_vals = sorted(set((v >> 7) & 1 for v in vals))
        vals_str = ", ".join(f"0x{v:02X}" for v in unique)
        bit7_str = ", ".join(str(b) for b in bit7_vals)
        # Show transition if values change within file
        if len(unique) > 1:
            first_val = vals[0]
            last_val = vals[-1]
            vals_str += f"  (first=0x{first_val:02X}, last=0x{last_val:02X})"
        print(f"  {label:<53} {vals_str:<30} bit7={bit7_str}")
    except FileNotFoundError:
        print(f"  {label:<53} FILE NOT FOUND")

print()
print("Interpretation:")
print("  bit 7 of byte[13] = 0 → Ozone mode Auto")
print("  bit 7 of byte[13] = 1 → Ozone mode Manual")
print()

# Also check byte 13 in non-ozone captures to understand the base value
print("Cross-check: byte[13] in other captures (non-ozone):")
other_files = [
    ("00 - Light ON baseline", "tools/captures_phase6/00_light_toggle_on_baseline.bin"),
    ("06 - Pump off→low baseline", "tools/captures_phase6/06_pump_off_to_low_baseline.bin"),
    ("15 - Blower ON baseline", "tools/captures_phase6/15_blower_on_baseline.bin"),
]
for label, path in other_files:
    try:
        frames = get_broadcasts(path)
        vals = sorted(set(f[13] for f in frames if len(f) > 13))
        vals_str = ", ".join(f"0x{v:02X}" for v in vals)
        print(f"  {label:<53} byte[13]={vals_str}")
    except FileNotFoundError:
        print(f"  {label:<53} FILE NOT FOUND")

