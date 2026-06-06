# Developer & Diagnostic Tools

This directory contains standalone Python utilities to help test connectivity, parse raw protocol data, and capture telemetry for reverse-engineering new Joyonway spa controllers.

All tools require **Python 3.10+** and use the standard library only — no external dependencies are needed.


## 1. Test Bridge Connectivity (`probe_spa.py`)

Verify that your RS-485 bridge (e.g., Elfin EW11) is reachable and successfully streaming data from the spa:

```bash
# Verify connection using environment defaults (loads from .env if present)
python3 tools/probe_spa.py

# Specify host and port directly
python3 tools/probe_spa.py --host 192.168.1.150 --port 8899
```


## 2. Capture Unmapped Bytes (`capture_unmapped_bytes.py`)

Analyze broadcast frames in real-time, filtering out known registers to reveal undocumented bytes:

```bash
# Capture 20 broadcast frames and analyze unmapped registers
SPA_BRIDGE_HOST="192.168.1.150" SPA_BRIDGE_PORT="8899" python3 tools/capture_unmapped_bytes.py --count 20
```

The script prints:
- A statistical breakdown of unmapped registers (min/max values, static vs. ticking bytes).
- The unique MD5 `unmapped_bytes_hash` fingerprint matching your controller firmware.


## 3. Parse and Diff Captures (`frame_parser_38400.py`)

Analyze captured raw `.bin` data packets and identify register changes between states:

```bash
# Parse a raw binary capture file
python3 tools/frame_parser_38400.py tools/captures/baseline/00_baseline_before.bin

# Compare/diff two captures to highlight register changes
python3 tools/frame_parser_38400.py --diff tools/captures/baseline/00_baseline_before.bin tools/captures/phase4/01_light_on.bin
```


## 4. Guided Interactive Capture (`guided_capture.py`)

Capture specific sequences of physical actions at the spa touchpad to gather deterministic RS-485 binary trace logs for analysis:

```bash
# Run the interactive capture tool
python3 tools/guided_capture.py
```

The script offers a menu to guide you through targeted transition runbooks:
- **Jets Transitions:** Guides through OFF → LOW → HIGH → LOW → OFF → HIGH → OFF transitions.
- **Heating & Circulation Sequence:** Captures heating loops and pump/jets dependencies.
- **Heater Mode Transitions:** Captures transitions between AUTO and MANUAL heating modes.
- **Real-time monitor:** Continuously prints incoming broadcasts parsed with the adapter logic.

Binary capture files are automatically saved with timestamped names in `tools/captures/` for analysis.


## ⚠️ Single-Client Connection Reminder
Most RS-485 bridges only accept a **single TCP client connection**. Before running these utilities, ensure that:
- The Home Assistant integration is temporarily disabled or stopped.
- Any mobile connections or other socket clients connected to the bridge are closed.
