#!/usr/bin/env python3
"""Analyze all schedule captures — compare flags bytes across slot 1/2."""
import sys, os, types
from pathlib import Path
from importlib.util import module_from_spec, spec_from_file_location

os.chdir(Path(__file__).resolve().parent.parent)
_comp_dir = Path("custom_components/joyonway")

def _load(name, path):
    spec = spec_from_file_location(name, path)
    mod = module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_pkg = types.ModuleType("joyonway")
_pkg.__path__ = [str(_comp_dir)]
sys.modules["joyonway"] = _pkg
_adapters_pkg = types.ModuleType("joyonway.adapters")
_adapters_pkg.__path__ = [str(_comp_dir / "adapters")]
sys.modules["joyonway.adapters"] = _adapters_pkg
_load("joyonway.adapters.base", _comp_dir / "adapters" / "base.py")
_load("joyonway.protocol", _comp_dir / "protocol.py")
_load("joyonway.adapters.p25b85", _comp_dir / "adapters" / "p25b85.py")

from joyonway.protocol import find_frames, is_broadcast, pseudo_unescape

captures_dir = "tools/captures_schedule_changes"
both_dir = "tools/captures_schedule_both"


def analyze_bin(filepath, label):
    with open(filepath, "rb") as f:
        data = f.read()
    frames = find_frames(data)
    broadcasts = [fr for fr in frames if is_broadcast(fr)]
    commands = [fr for fr in frames if not is_broadcast(fr)]
    print(f"\n--- {label} ---")
    print(f"  {len(data)} bytes, {len(frames)} frames ({len(broadcasts)} bc, {len(commands)} cmd)")
    sched_cmds = []
    for cmd_frame in commands:
        inner = cmd_frame[1:-1]
        unesc = pseudo_unescape(inner)
        if len(unesc) >= 16 and unesc[4] in (0xA3, 0xA4):
            sched_cmds.append((cmd_frame, unesc))

    if sched_cmds:
        for cmd_frame, unesc in sched_cmds:
            cmd_type = unesc[4]
            flags_byte = unesc[7]
            type_str = "heat_sched" if cmd_type == 0xA3 else "filter_sched"
            print(f"  SCHEDULE CMD: type={type_str}, flags=0x{flags_byte:02X}")
            print(f"    payload: {unesc[:16].hex()}")
            print(f"    slot1: {unesc[8]:02d}:{unesc[9]:02d}-{unesc[10]:02d}:{unesc[11]:02d}")
            print(f"    slot2: {unesc[12]:02d}:{unesc[13]:02d}-{unesc[14]:02d}:{unesc[15]:02d}")
            print(f"    wire: {cmd_frame.hex()}")
    else:
        print(f"  (no schedule commands found — only {len(commands)} bus polling frames)")


print("=" * 70)
print("INDIVIDUAL SLOT CAPTURES (each slot changed alone while disabled)")
print("=" * 70)
for fname in sorted(os.listdir(captures_dir)):
    if fname.endswith(".bin") and not fname.startswith("baseline") and "_post" not in fname:
        analyze_bin(os.path.join(captures_dir, fname), fname)

print()
print("=" * 70)
print("BOTH SLOTS CAPTURES (slot 1 + slot 2 changed together while disabled)")
print("=" * 70)
for fname in sorted(os.listdir(both_dir)):
    if fname.endswith(".bin") and not fname.startswith("baseline") and "_post" not in fname:
        analyze_bin(os.path.join(both_dir, fname), fname)

print()
print("=" * 70)
print("FLAGS BYTE SUMMARY")
print("=" * 70)

all_results = {}
for dpath in [captures_dir, both_dir]:
    for fname in sorted(os.listdir(dpath)):
        if fname.endswith(".bin") and not fname.startswith("baseline") and "_post" not in fname:
            with open(os.path.join(dpath, fname), "rb") as f:
                data = f.read()
            commands = [fr for fr in find_frames(data) if not is_broadcast(fr)]
            flags = []
            for cmd_frame in commands:
                unesc = pseudo_unescape(cmd_frame[1:-1])
                if len(unesc) >= 16 and unesc[4] in (0xA3, 0xA4):
                    flags.append(f"0x{unesc[7]:02X}")
            all_results[fname] = flags

for name, flags in all_results.items():
    print(f"  {name:45s} flags: {flags}")

print()
all_flags = [f for fl in all_results.values() for f in fl]
unique = set(all_flags)
print(f"All flags bytes seen: {unique}")

if len(unique) == 0:
    print("CONCLUSION: No schedule commands found in captures!")
elif len(unique) == 1:
    print(f"CONCLUSION: ALL captures use the SAME flags byte -> no slot 1 vs slot 2 difference!")
    print(f"  The panel uses {unique.pop()} regardless of which slot is being changed.")
    print(f"  The 'slot 2 quirk' applies equally to BOTH slots.")
else:
    print(f"CONCLUSION: DIFFERENT flags bytes found -> asymmetry exists!")
    s1_files = [n for n in all_results if "slot1" in n]
    s2_files = [n for n in all_results if "slot2" in n]
    both_files = [n for n in all_results if "both" in n]
    s1_flags = set(f for n in s1_files for f in all_results[n])
    s2_flags = set(f for n in s2_files for f in all_results[n])
    both_flags = set(f for n in both_files for f in all_results[n])
    print(f"  Slot 1 only:  {s1_flags}")
    print(f"  Slot 2 only:  {s2_flags}")
    print(f"  Both slots:   {both_flags}")


