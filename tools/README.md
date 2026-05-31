# Tools

Capture and analysis tools for Joyonway spa RS485 protocol (38400 baud, PB55x controllers).

**Requirements:** Python 3.10+, stdlib only — no `pip install` needed.

---

## Quick Start

### 1. Test bridge connectivity

Use the simple probe script first to verify your RS485 bridge is reachable:

```bash
python3 tools/probe_spa.py
```

### 2. Guided capture session (at the spa)

Interactive tool that walks you through capturing baseline → action → baseline for each equipment state:

```bash
# Full interactive session with defaults
python3 tools/guided_capture_38400.py

# Custom bridge address
python3 tools/guided_capture_38400.py --host 192.168.1.34 --port 8899

# Longer capture segments (15s each)
python3 tools/guided_capture_38400.py --duration 15

# Capture specific actions only
python3 tools/guided_capture_38400.py --actions light_on,pump_low,pump_high

# Custom output directory
python3 tools/guided_capture_38400.py --out-dir my_captures

# Dry run — simulates capture without connecting to bridge
python3 tools/guided_capture_38400.py --dry-run

# Resume automatically from an interrupted session in the same output directory
python3 tools/guided_capture_38400.py --out-dir my_captures
```

Output: raw `.bin` files + `session_manifest.json` in the output directory.

If `session_manifest.json` already exists and some action/phase segments are missing,
the guided tool automatically resumes from the next missing step and skips already
captured segments.

### 3. Parse and analyze captures

```bash
# Parse a single capture file (auto-detect model)
python3 tools/frame_parser_38400.py captures/00_baseline_before.bin

# Force a specific model byte map
python3 tools/frame_parser_38400.py --model p25b85 captures/00_baseline_before.bin

# Diff two captures (highlight byte changes across all broadcast frames)
python3 tools/frame_parser_38400.py --diff captures/00_baseline_before.bin captures/01_light_on_active.bin

# JSON output (for scripting)
python3 tools/frame_parser_38400.py --json captures/00_baseline_before.bin

# CSV output
python3 tools/frame_parser_38400.py --csv captures/00_baseline_before.bin

# Limit displayed frames
python3 tools/frame_parser_38400.py --max-frames 5 captures/00_baseline_before.bin
```

### 4. Run tests

```bash
python3 -m pytest tests/test_frame_protocol.py -v
```

---

## Available actions for capture

| Action      | Description                                             |
|-------------|---------------------------------------------------------|
| `baseline`  | Initial baseline — everything OFF                       |
| `light_on`  | Light ON — press light button (any color)               |
| `pump_low`  | Pump LOW — press pump button once (filtration)          |
| `pump_high` | Pump HIGH — press pump button again (massage jets)      |
| `heater`    | Heater active — raise setpoint above water temp         |
| `uv_lamp`   | UV lamp — activate UV/ozone if accessible               |
| `setpoint`  | Setpoint change — try different temperature values      |

---

## ⚠️ Bridge single-client limitation

Most RS485-to-WiFi bridges (Elfin EW11, USR-W610) only accept **one TCP client
at a time**. Before starting a capture session:

- Close the Elfin/USR phone app
- Stop Home Assistant's Joyonway integration
- Close any other tool connected to the bridge

---

## File overview

| File                       | Purpose                                          |
|----------------------------|--------------------------------------------------|
| `probe_spa.py`             | Quick connectivity test — dump raw frames        |
| `guided_capture_38400.py`  | Interactive guided capture tool                  |
| `frame_parser_38400.py`    | Frame parser, annotator, and diff tool           |

