#!/usr/bin/env python3
"""Rebuild session_manifest.json from existing .bin files."""
import datetime, json, os, re, sys

FRAME_START = 0x1A
FRAME_END = 0x1D
ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}

def pseudo_unescape(data):
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x1B and i+1 < len(data) and data[i+1] in ESCAPE_MAP:
            result.append(ESCAPE_MAP[data[i+1]]); i += 2
        else:
            result.append(data[i]); i += 1
    return bytes(result)

def find_frames(data):
    frames, i = [], 0
    while i < len(data):
        if data[i] == FRAME_START:
            j = i + 1
            while j < len(data):
                if data[j] == FRAME_END:
                    frames.append(data[i:j+1]); i = j+1; break
                j += 1
            else: break
        else: i += 1
    return frames

def analyze(data):
    frames = find_frames(data)
    bc = sum(1 for f in frames if len(f)>1 and f[1]==0xFF)
    state = None
    for f in frames:
        if len(f)>1 and f[1]==0xFF:
            u = f[:1] + pseudo_unescape(f[1:-1]) + f[-1:]
            if len(u) >= 30:
                state = {
                    "water_temp_f": u[9], "water_temp_c": round((u[9]-32)*5/9),
                    "pump_byte": f"0x{u[12]:02X}", "heater_byte": f"0x{u[14]:02X}",
                    "setpoint_f": u[16], "setpoint_c": round((u[16]-32)*5/9),
                    "light_byte": f"0x{u[17]:02X}",
                    "activity_byte": f"0x{u[28]:02X}" if len(u)>28 else "N/A",
                }
                break
    return len(frames), bc, len(frames)-bc, state

def main():
    d = sys.argv[1] if len(sys.argv)>1 else "tools/captures_phase5"
    bins = sorted(f for f in os.listdir(d) if f.endswith(".bin"))
    segments = []
    for fn in bins:
        m = re.match(r"^(\d+)_(.+)_(baseline|press|observe)\.bin$", fn)
        if not m: continue
        path = os.path.join(d, fn)
        data = open(path,"rb").read()
        mtime = os.path.getmtime(path)
        ts = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
        fc, bc, cc, state = analyze(data)
        segments.append({
            "action": m.group(2), "phase": m.group(3), "filename": fn,
            "started_at": (ts - datetime.timedelta(seconds=15)).isoformat(),
            "ended_at": ts.isoformat(),
            "duration_s": 15.0, "byte_count": len(data),
            "frame_count": fc, "broadcast_count": bc,
            "command_candidate_count": cc,
            "broadcast_state": state, "notes": "",
        })
    first_ts = segments[0]["started_at"] if segments else ""
    last_ts = segments[-1]["ended_at"] if segments else ""
    manifest = {
        "session": {
            "started_at": first_ts, "ended_at": last_ts,
            "host": "192.168.1.100", "port": 8899,
            "tool_version": "1.0.0-phase5",
            "phase": "phase5_extended_commands",
            "dry_run": False, "resumed": False, "completed": True,
        },
        "segments": segments,
    }
    out = os.path.join(d, "session_manifest.json")
    with open(out, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Rebuilt manifest: {out}")
    print(f"  {len(segments)} segments from {len(bins)} bin files")
    for s in segments:
        print(f"  {s['filename']:50s} {s['action']}/{s['phase']}")

if __name__ == "__main__":
    main()

