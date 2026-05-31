#!/usr/bin/env python3
"""
Phase 6 — Capture analysis for Joyonway P25B85.

Reads binary capture files + session_manifest.json from captures_phase6/,
decodes all frames, diffs baseline vs action vs observe, extracts command
frames, verifies CRC, and produces a detailed markdown report.
"""
from __future__ import annotations

import json
import os
import struct
import sys

# ── Protocol constants (duplicated from protocol.py for standalone use) ──

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_BYTE = 0x1B

ESCAPE_MAP: dict[int, int] = {
    0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E,
}

_CRC_POLY = 0x04C11DB7
_CRC_INIT = 0x00000000
_CRC_XOR_OUT = 0x552D22C8

_CRC_TABLE: list[int] = []
for _i in range(256):
    _crc = _i << 24
    for _ in range(8):
        _crc = ((_crc << 1) & 0xFFFFFFFF) ^ _CRC_POLY if _crc & 0x80000000 else (_crc << 1) & 0xFFFFFFFF
    _CRC_TABLE.append(_crc)


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


def unescape_frame(frame: bytes) -> bytes:
    return frame[:1] + pseudo_unescape(frame[1:-1]) + frame[-1:]


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


def _word32_swap(data: bytes) -> bytes:
    result = bytearray()
    for i in range(0, len(data), 4):
        result.extend(reversed(data[i:i + 4]))
    return bytes(result)


def compute_crc(payload: bytes) -> int:
    if len(payload) != 16:
        return 0
    msg = _word32_swap(payload)
    crc = _CRC_INIT
    for byte in msg:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC_TABLE[((crc >> 24) ^ byte) & 0xFF]
    return crc ^ _CRC_XOR_OUT


def verify_crc(frame_unescaped: bytes) -> bool | None:
    """Verify CRC of an unescaped command frame. Returns None if not applicable."""
    # Command frames: 1A [payload 16 bytes] [crc 4 bytes LE] 1D = 22 bytes unescaped
    if len(frame_unescaped) != 22:
        return None
    payload = frame_unescaped[1:17]  # bytes 1-16
    stored_crc = struct.unpack('<I', frame_unescaped[17:21])[0]
    computed = compute_crc(payload)
    return computed == stored_crc


# ── Broadcast frame parsing ──

HEATER_STATE_MAP = {
    0x40: "off", 0x50: "circulation", 0x55: "heating", 0x54: "heating",
    0x41: "disinfection", 0xC1: "disinfection", 0x48: "blower_active",
    0x51: "heating_standby",  # seen in captures
    0x58: "blower_active",
}

CMD_TYPE_MAP = {0xA1: "button", 0xA2: "datetime", 0xA3: "heat_sched", 0xA4: "filter_sched"}


def classify_frame(frame: bytes) -> str:
    if len(frame) > 1 and frame[1] == 0xFF:
        return "broadcast"
    if len(frame) > 2 and frame[1] == 0x01 and frame[2] == 0x20:
        return "panel_cmd"
    if len(frame) > 1 and frame[1] == 0x20:
        return "to_panel"
    return "other"


def parse_broadcast(frame_unesc: bytes) -> dict | None:
    if len(frame_unesc) < 30 or frame_unesc[1] != 0xFF:
        return None
    result = {
        "water_temp_f": frame_unesc[9],
        "water_temp_c": round((frame_unesc[9] - 32) * 5 / 9),
        "pump_byte": frame_unesc[12],
        "heater_byte": frame_unesc[14],
        "setpoint_f": frame_unesc[16],
        "setpoint_c": round((frame_unesc[16] - 32) * 5 / 9),
        "light_byte": frame_unesc[17],
        "activity_byte": frame_unesc[28] if len(frame_unesc) > 28 else 0,
    }
    if len(frame_unesc) > 36:
        result["heat_sched"] = frame_unesc[19:27]
        result["filter_sched"] = frame_unesc[29:37]
    if len(frame_unesc) > 58:
        result["clock"] = frame_unesc[53:59]
    return result


def decode_schedule(raw: bytes) -> str:
    """Decode 8 schedule bytes → human-readable."""
    if len(raw) < 8:
        return raw.hex()
    slot1_h = raw[0] & 0x3F
    slot1_en = "✅" if raw[0] & 0x40 else "❌"
    slot1 = f"{slot1_en} {slot1_h:02d}:{raw[1]:02d}-{raw[2]:02d}:{raw[3]:02d}"
    slot2_h = raw[4] & 0x3F
    slot2_en = "✅" if raw[4] & 0x40 else "❌"
    slot2 = f"{slot2_en} {slot2_h:02d}:{raw[5]:02d}-{raw[6]:02d}:{raw[7]:02d}"
    return f"S1[{slot1}] S2[{slot2}]"


def decode_clock(raw: bytes) -> str:
    if len(raw) < 6:
        return raw.hex()
    return f"20{raw[0]:02d}-{raw[1]:02d}-{raw[2]:02d} {raw[3]:02d}:{raw[4]:02d}:{raw[5]:02d}"


def fmt_heater(b: int) -> str:
    name = HEATER_STATE_MAP.get(b, "unknown")
    return f"0x{b:02X} ({name})"


def fmt_pump(b: int) -> str:
    if b == 0: return "0x00 (off)"
    if b == 0x02: return "0x02 (low)"
    if b == 0x04: return "0x04 (high)"
    return f"0x{b:02X}"


def fmt_light(b: int) -> str:
    if b & 0x01:
        return f"0x{b:02X} (ON)"
    return f"0x{b:02X} (off)"


def fmt_activity(b: int) -> str:
    flags = []
    if b & 0x08: flags.append("blower")
    if b & 0x20: flags.append("activity")
    return f"0x{b:02X} ({', '.join(flags) if flags else 'none'})"


def parse_command_frame(frame_unesc: bytes) -> dict | None:
    """Parse an unescaped command frame (22 bytes: 1A + 16 payload + 4 CRC + 1D)."""
    if len(frame_unesc) != 22:
        return None
    payload = frame_unesc[1:17]
    if payload[0] != 0x01 or payload[1] != 0x20:
        return None
    cmd_type_byte = payload[4]
    cmd_type = CMD_TYPE_MAP.get(cmd_type_byte, f"0x{cmd_type_byte:02X}")
    crc_ok = verify_crc(frame_unesc)
    result = {
        "cmd_type": cmd_type,
        "cmd_type_byte": cmd_type_byte,
        "payload_hex": payload.hex(),
        "crc_ok": crc_ok,
    }
    if cmd_type_byte == 0xA1:  # button
        result["pump_b8"] = payload[8]
        result["pump_b9"] = payload[9]
        result["btn_hi"] = payload[10]
        result["btn_lo"] = payload[11]
        result["setpoint_f"] = payload[15]
    elif cmd_type_byte == 0xA2:  # datetime
        result["datetime"] = {
            "year": 2000 + payload[8],
            "month": payload[9],
            "day": payload[10],
            "hour": payload[11],
            "minute": payload[12],
            "second": payload[13],
        }
    elif cmd_type_byte in (0xA3, 0xA4):  # schedule
        result["flags_byte"] = payload[7]
        result["slot1_start"] = (payload[8], payload[9])
        result["slot1_end"] = (payload[10], payload[11])
        result["slot2_start"] = (payload[12], payload[13])
        result["slot2_end"] = (payload[14], payload[15])
    return result


# ── Analysis engine ──

def load_segment(capture_dir: str, filename: str) -> bytes:
    path = os.path.join(capture_dir, filename)
    with open(path, "rb") as f:
        return f.read()


def analyze_segment(data: bytes) -> dict:
    """Analyze raw capture data, return structured results."""
    raw_frames = find_frames(data)

    broadcasts = []
    commands = []
    panel_cmds = []
    other_frames = []

    for rf in raw_frames:
        ftype = classify_frame(rf)
        unesc = unescape_frame(rf)

        if ftype == "broadcast":
            bc = parse_broadcast(unesc)
            if bc:
                broadcasts.append(bc)
        elif ftype == "panel_cmd":
            cmd = parse_command_frame(unesc)
            if cmd:
                cmd["raw_hex"] = rf.hex()
                cmd["unesc_hex"] = unesc.hex()
                panel_cmds.append(cmd)
            else:
                other_frames.append({"type": ftype, "raw": rf.hex(), "unesc": unesc.hex(), "len": len(unesc)})
        elif ftype == "to_panel":
            commands.append({"type": ftype, "raw": rf.hex(), "len": len(rf)})
        else:
            other_frames.append({"type": ftype, "raw": rf.hex(), "len": len(rf)})

    # Get first and last broadcast state
    first_bc = broadcasts[0] if broadcasts else None
    last_bc = broadcasts[-1] if broadcasts else None

    return {
        "total_frames": len(raw_frames),
        "broadcast_count": len(broadcasts),
        "panel_cmd_count": len(panel_cmds),
        "to_panel_count": len(commands),
        "other_count": len(other_frames),
        "first_broadcast": first_bc,
        "last_broadcast": last_bc,
        "panel_cmds": panel_cmds,
        "other_frames": other_frames,
    }


def diff_broadcasts(before: dict | None, after: dict | None) -> list[str]:
    if not before or not after:
        return ["(missing broadcast data)"]
    changes = []
    keys_format = {
        "water_temp_f": lambda v: f"{v}°F ({round((v-32)*5/9)}°C)",
        "pump_byte": fmt_pump,
        "heater_byte": fmt_heater,
        "setpoint_f": lambda v: f"{v}°F ({round((v-32)*5/9)}°C)",
        "light_byte": fmt_light,
        "activity_byte": fmt_activity,
    }
    for key in ["pump_byte", "heater_byte", "setpoint_f", "light_byte", "activity_byte", "water_temp_f"]:
        if before.get(key) != after.get(key):
            fmt = keys_format.get(key, lambda v: f"0x{v:02X}" if isinstance(v, int) else str(v))
            changes.append(f"{key}: {fmt(before[key])} → {fmt(after[key])}")

    # Schedule changes
    for sched_key, label in [("heat_sched", "heat schedule"), ("filter_sched", "filter schedule")]:
        b_s = before.get(sched_key)
        a_s = after.get(sched_key)
        if b_s and a_s and b_s != a_s:
            changes.append(f"{label}: {decode_schedule(b_s)} → {decode_schedule(a_s)}")

    # Clock changes (just note it changed, don't detail every second)
    b_c = before.get("clock")
    a_c = after.get("clock")
    if b_c and a_c and b_c != a_c:
        changes.append(f"clock: {decode_clock(b_c)} → {decode_clock(a_c)}")

    return changes if changes else ["(no changes)"]


def find_unique_panel_cmds(baseline_data: bytes, press_data: bytes) -> list[dict]:
    """Find panel commands in press that don't appear in baseline."""
    baseline_frames = find_frames(baseline_data)
    press_frames = find_frames(press_data)

    # Collect all non-broadcast frame hex from baseline
    baseline_set = set()
    for rf in baseline_frames:
        if classify_frame(rf) != "broadcast":
            baseline_set.add(rf.hex())

    # Find unique in press
    unique = []
    seen = set()
    for rf in press_frames:
        if classify_frame(rf) == "broadcast":
            continue
        h = rf.hex()
        if h not in baseline_set and h not in seen:
            seen.add(h)
            unesc = unescape_frame(rf)
            cmd = parse_command_frame(unesc)
            if cmd:
                cmd["raw_hex"] = h
                cmd["unesc_hex"] = unesc.hex()
                unique.append(cmd)
            else:
                unique.append({
                    "cmd_type": "unknown",
                    "raw_hex": h,
                    "unesc_hex": unesc.hex(),
                    "len": len(unesc),
                })
    return unique


# ── Report generator ──

def generate_report(capture_dir: str) -> str:
    manifest_path = os.path.join(capture_dir, "session_manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    segments = manifest["segments"]
    session = manifest["session"]

    lines = []
    w = lines.append

    w(f"# Phase 6 Capture Analysis Report")
    w(f"")
    w(f"**Session:** {session['started_at'][:19]} → {session['ended_at'][:19]} UTC")
    w(f"**Bridge:** {session['host']}:{session['port']}")
    w(f"**Segments:** {len(segments)}")
    w(f"**Completed:** {'✅' if session['completed'] else '❌'}")
    w(f"")

    # Group segments by action
    actions: dict[str, dict[str, dict]] = {}
    for seg in segments:
        action = seg["action"]
        phase = seg["phase"]
        if action not in actions:
            actions[action] = {}
        actions[action][phase] = seg

    w("## Summary Table")
    w("")
    w("| # | Action | Broadcast Changes | Unique Cmds | Notes |")
    w("|---|--------|-------------------|-------------|-------|")

    action_details = []

    for idx, (action_name, phases) in enumerate(actions.items(), 1):
        baseline_seg = phases.get("baseline")
        press_seg = phases.get("press")
        observe_seg = phases.get("observe")

        # Load data for detailed analysis
        baseline_data = load_segment(capture_dir, baseline_seg["filename"]) if baseline_seg else None
        press_data = load_segment(capture_dir, press_seg["filename"]) if press_seg else None
        observe_data = load_segment(capture_dir, observe_seg["filename"]) if observe_seg else None

        # Analyze each segment
        baseline_analysis = analyze_segment(baseline_data) if baseline_data else None
        press_analysis = analyze_segment(press_data) if press_data else None
        observe_analysis = analyze_segment(observe_data) if observe_data else None

        # Determine state before (last broadcast of baseline) and after (last broadcast of observe or press)
        state_before = baseline_analysis["last_broadcast"] if baseline_analysis else None
        state_after = (observe_analysis or press_analysis or {}).get("last_broadcast") if (observe_analysis or press_analysis) else None

        changes = diff_broadcasts(state_before, state_after)
        change_str = "; ".join(c for c in changes if c != "(no changes)")

        # Find unique panel commands
        unique_cmds = []
        if baseline_data and press_data:
            unique_cmds = find_unique_panel_cmds(baseline_data, press_data)

        notes = ""
        for p in phases.values():
            if p.get("notes"):
                notes = p["notes"]
                break

        w(f"| {idx} | **{action_name}** | {change_str or '—'} | {len(unique_cmds)} | {notes[:60] if notes else ''} |")

        action_details.append({
            "name": action_name,
            "phases": phases,
            "baseline": baseline_analysis,
            "press": press_analysis,
            "observe": observe_analysis,
            "changes": changes,
            "unique_cmds": unique_cmds,
            "state_before": state_before,
            "state_after": state_after,
        })

    # Detailed per-action analysis
    w("")
    w("---")
    w("")
    w("## Detailed Analysis Per Action")

    for detail in action_details:
        w("")
        w(f"### {detail['name']}")
        w("")

        # State before/after
        sb = detail["state_before"]
        sa = detail["state_after"]
        if sb:
            w(f"**Before:** temp={sb['water_temp_f']}°F/{sb.get('water_temp_c','')}°C "
              f"set={sb['setpoint_f']}°F pump={fmt_pump(sb['pump_byte'])} "
              f"heater={fmt_heater(sb['heater_byte'])} light={fmt_light(sb['light_byte'])} "
              f"activity={fmt_activity(sb['activity_byte'])}")
            if sb.get("heat_sched"):
                w(f"  Heat: {decode_schedule(sb['heat_sched'])}")
            if sb.get("filter_sched"):
                w(f"  Filter: {decode_schedule(sb['filter_sched'])}")

        if sa and sb:
            changes = detail["changes"]
            if any(c != "(no changes)" for c in changes):
                w(f"")
                w(f"**Changes (baseline → after):**")
                for c in changes:
                    if c != "(no changes)":
                        w(f"- {c}")
            else:
                w(f"")
                w(f"**No broadcast state changes detected.**")

        # Unique commands found
        ucmds = detail["unique_cmds"]
        if ucmds:
            w("")
            w(f"**Unique command frames in press ({len(ucmds)}):**")
            w("")
            for ci, cmd in enumerate(ucmds):
                if cmd.get("cmd_type") in ("button", "datetime", "heat_sched", "filter_sched"):
                    crc_str = "✅ CRC OK" if cmd.get("crc_ok") else ("❌ CRC FAIL" if cmd.get("crc_ok") is False else "? CRC N/A")
                    w(f"  {ci+1}. **{cmd['cmd_type']}** [{crc_str}]")
                    w(f"     Raw wire: `{cmd['raw_hex']}`")
                    w(f"     Payload:  `{cmd.get('payload_hex', '')}`")
                    if cmd["cmd_type"] == "button":
                        w(f"     pump_b8=0x{cmd['pump_b8']:02X} pump_b9=0x{cmd['pump_b9']:02X} "
                          f"btn=0x{cmd['btn_hi']:02X},0x{cmd['btn_lo']:02X} setpoint={cmd['setpoint_f']}°F")
                    elif cmd["cmd_type"] == "datetime":
                        dt = cmd["datetime"]
                        w(f"     DateTime: {dt['year']}-{dt['month']:02d}-{dt['day']:02d} "
                          f"{dt['hour']:02d}:{dt['minute']:02d}:{dt['second']:02d}")
                    elif cmd["cmd_type"] in ("heat_sched", "filter_sched"):
                        w(f"     Flags: 0x{cmd['flags_byte']:02X}")
                        w(f"     Slot1: {cmd['slot1_start'][0]:02d}:{cmd['slot1_start'][1]:02d} - "
                          f"{cmd['slot1_end'][0]:02d}:{cmd['slot1_end'][1]:02d}")
                        w(f"     Slot2: {cmd['slot2_start'][0]:02d}:{cmd['slot2_start'][1]:02d} - "
                          f"{cmd['slot2_end'][0]:02d}:{cmd['slot2_end'][1]:02d}")
                else:
                    w(f"  {ci+1}. **{cmd.get('cmd_type','?')}** (len={cmd.get('len','?')})")
                    w(f"     Raw: `{cmd['raw_hex']}`")
        else:
            # Check if there were any non-broadcast frames at all
            pa = detail.get("press")
            if pa:
                w(f"")
                w(f"**No unique command frames** (all non-broadcast frames also appear in baseline)")
                w(f"  Press: {pa['panel_cmd_count']} panel cmds, {pa['to_panel_count']} to-panel, {pa['other_count']} other")

        # Notes
        for p in detail["phases"].values():
            if p.get("notes"):
                w(f"")
                w(f"> **Note:** {p['notes']}")

    # ── Key findings section ──
    w("")
    w("---")
    w("")
    w("## Key Findings")
    w("")

    # 1. Analyze schedule enable/disable
    w("### Schedule Enable/Disable Encoding")
    w("")
    for action_name in ["heat_schedule_enable", "heat_schedule_disable", "filter_schedule_enable", "filter_schedule_disable"]:
        for d in action_details:
            if d["name"] == action_name:
                sb = d["state_before"]
                sa = d["state_after"]
                if sb and sa:
                    sched_key = "heat_sched" if "heat" in action_name else "filter_sched"
                    before_raw = sb.get(sched_key)
                    after_raw = sa.get(sched_key)
                    if before_raw and after_raw:
                        w(f"**{action_name}:**")
                        w(f"  Before: `{before_raw.hex()}` → {decode_schedule(before_raw)}")
                        w(f"  After:  `{after_raw.hex()}` → {decode_schedule(after_raw)}")
                        # Byte-level diff
                        diffs = []
                        for i in range(min(len(before_raw), len(after_raw))):
                            if before_raw[i] != after_raw[i]:
                                diffs.append(f"byte[{i}]: 0x{before_raw[i]:02X} → 0x{after_raw[i]:02X} (delta=0x{before_raw[i]^after_raw[i]:02X})")
                        if diffs:
                            w(f"  Changed bytes: {', '.join(diffs)}")
                        w("")

    # 2. Heater byte values observed
    w("### Heater Byte (byte 14) Values Observed")
    w("")
    heater_values: dict[int, list[str]] = {}
    for d in action_details:
        for phase_name in ["baseline", "observe"]:
            analysis = d.get(phase_name)
            if analysis and analysis.get("last_broadcast"):
                hb = analysis["last_broadcast"]["heater_byte"]
                if hb not in heater_values:
                    heater_values[hb] = []
                heater_values[hb].append(f"{d['name']}/{phase_name}")
    for val, contexts in sorted(heater_values.items()):
        w(f"- `0x{val:02X}` ({HEATER_STATE_MAP.get(val, 'unknown')}): seen in {', '.join(contexts[:5])}" +
          (f"... +{len(contexts)-5} more" if len(contexts) > 5 else ""))
    w("")

    # 3. Ozone analysis
    w("### Ozone / Disinfection")
    w("")
    for action_name in ["ozone_mode_auto", "ozone_mode_manual", "ozone_manual_on", "ozone_manual_off"]:
        for d in action_details:
            if d["name"] == action_name:
                changes = [c for c in d["changes"] if c != "(no changes)"]
                w(f"**{action_name}:** {'  '.join(changes) if changes else 'no broadcast changes'}")
    w("")

    # 4. Panel-local actions
    w("### Panel-Local Actions (No RS485 Traffic Expected)")
    w("")
    for action_name in ["panel_auto_lock", "panel_brightness", "panel_screen_flip"]:
        for d in action_details:
            if d["name"] == action_name:
                changes = [c for c in d["changes"] if c != "(no changes)"]
                has_unique = len(d["unique_cmds"]) > 0
                w(f"- **{action_name}:** broadcast changes={'yes: '+'; '.join(changes) if changes else 'none'}, unique cmds={'yes' if has_unique else 'none'}")
    w("")

    # 5. Command frame CRC verification
    w("### CRC Verification of Captured Commands")
    w("")
    total_cmds = 0
    crc_ok = 0
    crc_fail = 0
    crc_na = 0
    for d in action_details:
        for cmd in d["unique_cmds"]:
            total_cmds += 1
            if cmd.get("crc_ok") is True:
                crc_ok += 1
            elif cmd.get("crc_ok") is False:
                crc_fail += 1
                w(f"  ❌ FAIL: {d['name']} — `{cmd.get('raw_hex','')[:40]}...`")
            else:
                crc_na += 1
    w(f"- Total unique commands found: {total_cmds}")
    w(f"- CRC verified OK: {crc_ok}")
    w(f"- CRC failed: {crc_fail}")
    w(f"- CRC not applicable: {crc_na}")
    w("")

    return "\n".join(lines)


def main():
    capture_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "captures_phase6")

    if not os.path.isdir(capture_dir):
        print(f"Error: directory not found: {capture_dir}", file=sys.stderr)
        sys.exit(1)

    manifest_path = os.path.join(capture_dir, "session_manifest.json")
    if not os.path.isfile(manifest_path):
        print(f"Error: session_manifest.json not found in {capture_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing captures in {capture_dir}...", file=sys.stderr)
    report = generate_report(capture_dir)

    # Write report
    report_path = os.path.join(capture_dir, "analysis_report.md")
    with open(report_path, "w") as f:
        f.write(report)
    print(f"Report written to {report_path}", file=sys.stderr)

    # Also print to stdout
    print(report)


if __name__ == "__main__":
    main()

