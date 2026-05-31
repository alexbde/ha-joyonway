#!/usr/bin/env python3
"""
Frame parser / analyzer for Joyonway spa RS485 captures (38400 baud).

Parses .bin capture files into individual frames, applies model-specific
pseudo-unescape policies, and displays annotated byte maps. Supports diff
mode for comparing two captures.

Python stdlib only — no pip dependencies required.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from typing import Any

__version__ = "1.0.0"

# ──────────────────────────────────────────────────────────────
# Protocol constants
# ──────────────────────────────────────────────────────────────

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

# Pseudo-escape table: escaped pair suffix → original byte
ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E,
}

MODEL_SIGNATURES: dict[int, str] = {0x03: "P25B85", 0x02: "P23B32"}

HEATER_STATES = {
    0x00: "off", 0x50: "circulation", 0x54: "heating",
    0x40: "cooldown", 0xC1: "UV/ozone",
}

# ──────────────────────────────────────────────────────────────
# Core protocol functions
# ──────────────────────────────────────────────────────────────

def find_frames(stream: bytes) -> list[bytes]:
    """Extract frames delimited by 0x1A ... 0x1D from a raw byte stream."""
    frames: list[bytes] = []
    i = 0
    n = len(stream)
    while i < n:
        if stream[i] == FRAME_START:
            j = i + 1
            while j < n:
                if stream[j] == FRAME_END:
                    frames.append(stream[i : j + 1])
                    i = j + 1
                    break
                j += 1
            else:
                break
        else:
            i += 1
    return frames


def pseudo_unescape(data: bytes) -> bytes:
    """Reverse pseudo-escape encoding."""
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


def unescape_frame(frame: bytes, policy: str) -> bytes:
    """Apply unescape policy to a frame."""
    if policy == "full":
        return frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]
    elif policy == "tail":
        if len(frame) > 55:
            return frame[:55] + pseudo_unescape(frame[55:-1]) + frame[-1:]
        return frame
    return frame


def detect_model(frame: bytes) -> str | None:
    if len(frame) > 8:
        return MODEL_SIGNATURES.get(frame[8])
    return None


def get_unescape_policy(model: str | None) -> str:
    if model == "P25B85":
        return "full"
    elif model == "P23B32":
        return "tail"
    return "none"


def fahrenheit_to_celsius(f: int) -> float | None:
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9, 1)


def is_broadcast(frame: bytes) -> bool:
    return len(frame) > 1 and frame[1] == 0xFF


def check_escape_positions(raw_frame: bytes) -> list[tuple[int, int, int]]:
    """Find escape sequences. Returns list of (raw_index, suffix, original_byte)."""
    positions = []
    i = 0
    while i < len(raw_frame) - 1:
        if raw_frame[i] == ESCAPE_BYTE:
            suffix = raw_frame[i + 1]
            if suffix in ESCAPE_MAP:
                positions.append((i, suffix, ESCAPE_MAP[suffix]))
                i += 2
                continue
        i += 1
    return positions


# ──────────────────────────────────────────────────────────────
# Byte map annotations
# ──────────────────────────────────────────────────────────────

def annotate_p25b85(frame: bytes) -> list[dict]:
    annotations = []
    def add(idx, name, value_fn=None):
        if idx < len(frame):
            v = frame[idx]
            e = {"byte": idx, "name": name, "raw": f"0x{v:02X}"}
            if value_fn:
                e["decoded"] = value_fn(v)
            annotations.append(e)
    add(8, "model_id", lambda v: MODEL_SIGNATURES.get(v, f"unknown(0x{v:02X})"))
    add(9, "water_temp_F", lambda v: f"{v}°F = {fahrenheit_to_celsius(v)}°C")
    add(12, "pump_candidate_1")
    add(13, "pump_candidate_2")
    add(15, "heater_state", lambda v: HEATER_STATES.get(v, f"unknown(0x{v:02X})"))
    add(16, "setpoint_F", lambda v: f"{v}°F = {fahrenheit_to_celsius(v)}°C")
    add(18, "light_flags", lambda v: f"light={'ON' if v & 0x01 else 'OFF'}, flag_0x80={'SET' if v & 0x80 else 'clear'}")
    add(29, "uv_ozone_flag", lambda v: f"UV={'ACTIVE' if v & 0x20 else 'off'}")
    if len(frame) > 58:
        dt = frame[53:59]
        try:
            dt_str = f"20{dt[0]:02d}-{dt[1]:02d}-{dt[2]:02d} {dt[3]:02d}:{dt[4]:02d}:{dt[5]:02d}"
        except (IndexError, ValueError):
            dt_str = "parse error"
        annotations.append({"byte": "53-58", "name": "datetime",
                            "raw": " ".join(f"0x{b:02X}" for b in dt), "decoded": dt_str})
    return annotations


def annotate_p23b32(frame: bytes) -> list[dict]:
    annotations = []
    def add(idx, name, value_fn=None):
        if idx < len(frame):
            v = frame[idx]
            e = {"byte": idx, "name": name, "raw": f"0x{v:02X}"}
            if value_fn:
                e["decoded"] = value_fn(v)
            annotations.append(e)
    add(8, "model_id", lambda v: MODEL_SIGNATURES.get(v, f"unknown(0x{v:02X})"))
    add(9, "water_temp_F", lambda v: f"{v}°F = {fahrenheit_to_celsius(v)}°C")
    add(12, "pump_byte1", lambda v: f"left_jets={'ON' if v & 0x04 else 'off'}, right_jets={'ON' if v & 0x10 else 'off'}")
    add(14, "pump_byte2", lambda v: f"filtration={'ON' if v & 0x01 else 'off'}, blower={'ON' if v & 0x08 else 'off'}, heater={'ON' if v & 0x10 else 'off'}")
    add(16, "setpoint_F", lambda v: f"{v}°F = {fahrenheit_to_celsius(v)}°C")
    add(17, "light_byte", lambda v: f"light={'ON' if v & 0x01 else 'off'}, filtration_flag={'SET' if v & 0x80 else 'clear'}")
    return annotations


def annotate_frame(frame: bytes, model: str | None) -> list[dict]:
    if model == "P25B85":
        return annotate_p25b85(frame)
    elif model == "P23B32":
        return annotate_p23b32(frame)
    return []


# ──────────────────────────────────────────────────────────────
# Display
# ──────────────────────────────────────────────────────────────

def hex_dump(data: bytes, per_line: int = 16) -> str:
    lines = []
    for off in range(0, len(data), per_line):
        chunk = data[off : off + per_line]
        lines.append(f"  [{off:3d}]  {' '.join(f'{b:02X}' for b in chunk)}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Parse mode
# ──────────────────────────────────────────────────────────────

def parse_file(filepath: str, model_arg: str, unescape_arg: str,
               max_frames: int | None) -> dict[str, Any]:
    with open(filepath, "rb") as f:
        data = f.read()
    raw_frames = find_frames(data)
    broadcasts = [fr for fr in raw_frames if is_broadcast(fr)]
    result: dict[str, Any] = {
        "file": filepath,
        "total_bytes": len(data),
        "total_frames": len(raw_frames),
        "broadcast_frames": len(broadcasts),
        "non_broadcast_frames": len(raw_frames) - len(broadcasts),
        "frames": [],
    }
    display = broadcasts[:max_frames] if max_frames else broadcasts
    for idx, raw_frame in enumerate(display):
        model = None
        if model_arg == "auto":
            model = detect_model(raw_frame)
        elif model_arg == "p25b85":
            model = "P25B85"
        elif model_arg == "p23b32":
            model = "P23B32"
        policy = unescape_arg if unescape_arg != "auto" else get_unescape_policy(model)
        escapes = check_escape_positions(raw_frame)
        logical = unescape_frame(raw_frame, policy)
        fi: dict[str, Any] = {
            "index": idx, "model": model, "unescape_policy": policy,
            "raw_length": len(raw_frame), "logical_length": len(logical),
            "escape_count": len(escapes), "raw_hex": raw_frame.hex(" "),
        }
        if escapes:
            fi["escapes"] = [{"raw_index": p, "sequence": f"0x1B 0x{s:02X}",
                              "original": f"0x{o:02X}"} for p, s, o in escapes]
            fi["logical_hex"] = logical.hex(" ")
        fi["annotations"] = annotate_frame(logical, model)
        result["frames"].append(fi)
    return result


def print_parse_result(result: dict):
    print(f"\n{'═' * 70}")
    print(f"  File: {result['file']}")
    print(f"  Total: {result['total_bytes']} bytes, {result['total_frames']} frames "
          f"({result['broadcast_frames']} broadcast, {result['non_broadcast_frames']} other)")
    print(f"{'═' * 70}")
    for fi in result["frames"]:
        model_str = fi['model'] or 'unknown'
        print(f"\n── Broadcast #{fi['index']} (model: {model_str}, unescape: {fi['unescape_policy']}) ──")
        print(f"  Raw: {fi['raw_length']} bytes, Logical: {fi['logical_length']} bytes")
        if fi.get("escapes"):
            print(f"  Escape sequences ({fi['escape_count']}):")
            for esc in fi["escapes"]:
                print(f"    raw[{esc['raw_index']}]: {esc['sequence']} → {esc['original']}")
            print(f"\n  Logical hex (after unescape):")
            print(hex_dump(bytes.fromhex(fi["logical_hex"].replace(" ", ""))))
        else:
            print(f"\n  Hex:")
            print(hex_dump(bytes.fromhex(fi["raw_hex"].replace(" ", ""))))
        if fi.get("annotations"):
            print(f"\n  Decoded fields:")
            for a in fi["annotations"]:
                dec = f" → {a['decoded']}" if "decoded" in a else ""
                print(f"    byte[{a['byte']}] {a['name']}: {a['raw']}{dec}")


# ──────────────────────────────────────────────────────────────
# Diff mode
# ──────────────────────────────────────────────────────────────

def diff_files(file_a: str, file_b: str, model_arg: str, unescape_arg: str) -> dict:
    def load(fp):
        with open(fp, "rb") as f:
            data = f.read()
        result = []
        for raw in find_frames(data):
            if not is_broadcast(raw):
                continue
            m = detect_model(raw) if model_arg == "auto" else (
                "P25B85" if model_arg == "p25b85" else
                "P23B32" if model_arg == "p23b32" else None)
            p = unescape_arg if unescape_arg != "auto" else get_unescape_policy(m)
            result.append((unescape_frame(raw, p), m))
        return result

    fa, fb = load(file_a), load(file_b)
    max_len = max((len(f) for f, _ in fa + fb), default=0)
    diffs = []
    for bi in range(max_len):
        va: dict[int, int] = {}
        vb: dict[int, int] = {}
        for f, _ in fa:
            if bi < len(f):
                va[f[bi]] = va.get(f[bi], 0) + 1
        for f, _ in fb:
            if bi < len(f):
                vb[f[bi]] = vb.get(f[bi], 0) + 1
        if va != vb:
            diffs.append({
                "byte": bi,
                "file_a": {f"0x{k:02X}": v for k, v in sorted(va.items())},
                "file_b": {f"0x{k:02X}": v for k, v in sorted(vb.items())},
            })
    model = fa[0][1] if fa else (fb[0][1] if fb else None)
    return {"file_a": file_a, "file_b": file_b,
            "broadcasts_a": len(fa), "broadcasts_b": len(fb),
            "model": model, "differing_positions": diffs}


def print_diff_result(r: dict):
    print(f"\n{'═' * 70}")
    print(f"  DIFF: Broadcast frame byte comparison")
    print(f"  File A: {r['file_a']} ({r['broadcasts_a']} broadcasts)")
    print(f"  File B: {r['file_b']} ({r['broadcasts_b']} broadcasts)")
    print(f"  Model:  {r['model'] or 'unknown'}")
    print(f"{'═' * 70}")
    diffs = r["differing_positions"]
    if not diffs:
        print("\n  ✅ No differences found.")
        return
    p25_labels = {9: "water_temp", 12: "pump_cand1", 13: "pump_cand2",
                  15: "heater", 16: "setpoint", 18: "light", 29: "uv"}
    p23_labels = {9: "water_temp", 12: "pump1", 14: "pump2", 16: "setpoint", 17: "light"}
    labels = p25_labels if r["model"] == "P25B85" else p23_labels if r["model"] == "P23B32" else {}
    print(f"\n  {len(diffs)} byte position(s) differ:\n")
    print(f"  {'Byte':>6}  {'File A values':30}  {'File B values':30}")
    print(f"  {'─' * 6}  {'─' * 30}  {'─' * 30}")
    for h in diffs:
        a_s = ", ".join(f"{k}×{v}" for k, v in h["file_a"].items()) or "(empty)"
        b_s = ", ".join(f"{k}×{v}" for k, v in h["file_b"].items()) or "(empty)"
        lb = f"  ← {labels[h['byte']]}" if h["byte"] in labels else ""
        print(f"  [{h['byte']:4d}]  {a_s:30}  {b_s:30}{lb}")


# ──────────────────────────────────────────────────────────────
# CSV output
# ──────────────────────────────────────────────────────────────

def write_csv(result: dict, output=sys.stdout):
    w = csv.writer(output)
    w.writerow(["frame_index", "model", "raw_length", "logical_length",
                "byte_index", "field_name", "raw_value", "decoded"])
    for fi in result.get("frames", []):
        for a in fi.get("annotations", []):
            w.writerow([fi["index"], fi.get("model", ""), fi["raw_length"],
                        fi["logical_length"], a["byte"], a["name"],
                        a["raw"], a.get("decoded", "")])


# ──────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Parse and analyze Joyonway spa RS485 capture files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s capture.bin                          # Auto-detect model
  %(prog)s --model p25b85 capture.bin           # Force P25B85 byte map
  %(prog)s --diff before.bin after.bin          # Compare two captures
  %(prog)s --json capture.bin                   # JSON output
  %(prog)s --csv capture.bin                    # CSV output
""")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.add_argument("--model", choices=["p25b85", "p23b32", "auto"], default="auto")
    p.add_argument("--unescape", choices=["full", "tail", "none", "auto"], default="auto")
    p.add_argument("--diff", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--csv", action="store_true", dest="csv_output")
    p.add_argument("--max-frames", type=int, default=None)
    return p.parse_args()


def main():
    args = parse_args()
    if args.diff:
        if len(args.files) != 2:
            print("Error: --diff requires exactly 2 files", file=sys.stderr)
            sys.exit(1)
        result = diff_files(args.files[0], args.files[1], args.model, args.unescape)
        if args.json:
            json.dump(result, sys.stdout, indent=2)
            print()
        else:
            print_diff_result(result)
    else:
        for fp in args.files:
            result = parse_file(fp, args.model, args.unescape, args.max_frames)
            if args.json:
                json.dump(result, sys.stdout, indent=2)
                print()
            elif args.csv_output:
                write_csv(result)
            else:
                print_parse_result(result)


if __name__ == "__main__":
    main()

