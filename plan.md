# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller,
> with a model adapter interface ready for future multi-model expansion.
>
> **Repo:** `alexbde/ha-joyonway-p25b85` — independent from upstream
> **Upstream:** christopheknap keeps `ha-joyonway-p23b32` P23B32-only.
> We removed `joyonway_p23b32` from our repo (HACS requires single
> integration per repo). His code remains at
> https://github.com/KnapTheBuilder/ha-joyonway-p23b32 for reference.
> Multi-model umbrella revisit in ~6 months.
>
> **Integration domain:** `joyonway_p25b85`
> **Hardware:** P25B85 + PB554 + Elfin EW11
> **Status:** Phase 4 write support implemented (light + pump). Temperature lookup table complete (31 frames).

---

## 0. AI Instructions

- **No PII / timestamps in code.** Do NOT add dates, author names, usernames,
  IP addresses, or any data that could identify the developer or when work was
  done. Dates belong only in this plan file and in git history — never in
  `.py`, `.json`, or other shipped files.
- This plan file is the single source of truth for the AI. Read it at the
  start of every session.
- **End-of-session routine.** When the user says "end this session" (or
  similar), before finishing:
  1. Write any new findings, decisions, or context into this plan file so a
     fresh session can pick up without loss.
  2. Remove redundant, outdated, or already-completed information to keep
     the file concise and the mental load small.
  3. Verify the plan file is self-contained — a new AI session with no
     prior context should be able to read it and continue the project.

---

## 1. Hardware

- **Spa:** Home Deluxe White Marble (outdoor whirlpool, rigid/hardshell)
- **Controller:** Joyonway P25B85, PCB `P2325B0003 R05`
- **Touchpad:** PB554 colour screen
- **Bridge:** Elfin EW11, RS-485 → WiFi, TCP server (IP in `.env`, port 8899)
- **UART:** 38400 8N1
- **Pump:** ONE dual-speed (low = filtration, high = massage jets, 20-min auto-off)
- **Light:** RGB LED, 9 states cycling via button
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone:** UV lamp on ozonator connector (manual calls it "Ozonauslass")
- **Blower:** connector exists but NOT wired

---

## 2. Protocol Summary

### Framing

- 38400 8N1, start `0x1A`, end `0x1D`
- Pseudo-escape: `0x1B XX` sequences (see escape table in code)
- P25B85: full-frame unescape. P23B32: tail-only (bytes 55+).
- Frame boundaries detected on raw bytes FIRST, then unescape applied.
- **byte[3]** = payload length (unescaped bytes between delimiters, excluding 4-byte CRC)

### Bus cycle (~600ms total, 12 frames)

Broadcast at `0xFF` every cycle = main data source (~16 broadcasts per 10s).

### Broadcast byte map (P25B85, logical frame after unescape)

| Byte | Content | Status |
|------|---------|--------|
| 8 | Model ID (`0x03` = P25B85) | ✅ confirmed |
| **9** | Water temperature (°F) | ✅ confirmed |
| **12** | Pump status (`0x02`=low, `0x04`=high) | ✅ confirmed |
| **14** | Heater state (`0x40`/`0x50`/`0x55`/`0x41`) | ✅ confirmed |
| **16** | Setpoint (°F) | ✅ confirmed |
| **17** | Light flags (bit 0 = light ON, bit 7 = cycle flag) | ✅ confirmed |
| **27** | Pump mirror (same as byte 12) | ✅ confirmed |
| **28** | Activity flag (`0x20` during heating AND UV) | ✅ confirmed |
| 53–58 | Date/time (year, month, day, hour, minute, second) | ✅ confirmed |

### Command frame structure (22 bytes, captured from PB554 panel)

```
Offset  Size  Field
------  ----  -----
0       1     Frame start (0x1A)
1       1     Destination (0x01 = controller)
2-7     6     Header (0x20 0x10 0x3C 0xA1 0x10 0xA1) — fixed
8       1     Pump byte 1 (encodes transition)
9       1     Pump byte 2 (encodes transition)
10      1     Button flag high (0x40=light, 0x80=temp, 0x00=pump)
11      1     Button flag low  (same as byte 10)
12-14   3     Fixed (0x00 0xC0 0x00)
15      1     Setpoint temperature (°F) — current at time of capture
16      1     Fixed (0x00)
17-20   4     CRC (proprietary, not cracked)
21      1     Frame end (0x1D)
```

### Captured command frames (light + pump, from Phase 4 captures)

| Action | Frame (hex) |
|--------|-------------|
| Light toggle | `1a0120103ca110a10000404000c00056003031eeb21d` |
| Pump OFF→low | `1a0120103ca110a10202000000c00056007dd2146b1d` |
| Pump low→high | `1a0120103ca110a10604000000c0005600fc1221c61d` |
| Pump high→OFF | `1a0120103ca110a10400000000c0005600735738e91d` |

Temperature frames: 31 frames in `tools/captures_temp/temp_commands.json` (10-40°C).

Key findings:
- Light ON and OFF use the **same frame** — it's a **toggle** command
- Pump commands encode **state transitions** (must match current state)
- Temp commands include **target °F in byte 15** + button flag 0x80
- CRC is 4 bytes, algorithm is **proprietary** (not CRC-32, CRC-32C, Modbus,
  or any standard variant tested)

### CRC safety

- ❌ NEVER send frames with forged CRC (can activate heater — KDy warning)
- ✅ ONLY replay verbatim captured frames from physical panel
- CRC is linear (proven) but uses non-standard/session-dependent processing
- All needed frames (light, pump, temperature 10-40°C) are captured
- See `docs/crc_analysis.md` for full CRC analysis

---

## 3. Current Implementation

### File structure

```
custom_components/joyonway_p25b85/
├── __init__.py          # entry setup, coordinator creation
├── const.py             # domain, config keys, defaults
├── manifest.json        # HACS-compatible, v0.1.0
├── config_flow.py       # IP + port, TCP connection test
├── protocol.py          # find_frames, pseudo_unescape, validate_frame
├── coordinator.py       # async TCP polling + async_send_command
├── sensor.py            # adapter-driven (translation_key based)
├── binary_sensor.py     # adapter-driven + bridge connectivity
├── switch.py            # light toggle (on/off via replay)
├── button.py            # pump cycle + pump off
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), command frames, pump logic
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Write support entities (Phase 4)

| Entity | Platform | Type | Action |
|--------|----------|------|--------|
| Light | switch | on/off | Sends toggle when state needs changing |
| Pump cycle | button | press | Advances pump: off→low→high→off |
| Pump off | button | press | Turns pump off (handles low→high→off if needed) |

### Entity translations use `translation_key`

Entity names come from `translations/*.json` under `entity.<platform>.*` keys.

### Live-validated (Phase 2-3)

- ✅ Integration installs via HACS custom repo
- ✅ Config flow connects to EW11 and creates device
- ✅ All read sensors work (water temp, setpoint, pump, light, heater, UV)
- ✅ German/English/French translations work

### Write support — NOT YET LIVE TESTED

- ⚠️ Light switch: implemented, needs live test at spa
- ⚠️ Pump buttons: implemented, needs live test at spa
- ⚠️ Temperature: lookup table complete (31 frames, 10-40°C), climate entity not yet built

---

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Capture tools | ✅ Done | `guided_capture_38400.py`, `frame_parser_38400.py` |
| 2. Integration | ✅ Done | Deployed, reading live data, HACS install works |
| 3. Validate byte map | ✅ Done | All byte positions confirmed from captures |
| 4. Write commands | ✅ Implemented | Light + pump replay; temp lookup table complete |
| 5. Live test writes | **Next** | Test light switch and pump buttons at spa |
| 6. Temperature control | **Next** | Lookup table done, implement climate entity |
| 7. Polish & release | Planned | After Phase 5+6 |

---

## 5. Next Steps

1. **Live test write commands** — restart HA with new code, test light switch
   and pump buttons. Verify state updates after commands.
2. **Implement temperature control** — lookup table is complete at
   `tools/captures_temp/temp_commands.json` (31 frames, 10-40°C).
   Add a `climate` entity:
   - `hvac_modes=[HEAT]` (spa always heats to setpoint)
   - `current_temperature` → water temp sensor (byte 9)
   - `target_temperature` → setpoint (byte 16), sends matching frame from lookup
   - `min_temp=10`, `max_temp=40`, step 1°C
   - Decision: go with `climate` for the visual UX (thermostat ring card)
3. **PR to frame-analyzer** — add P25B85 preset to christopheknap's tool
4. **Polish** — version bump, README update, HACS release

---

## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **HACS** requires single integration per repo.
- **Restart required** after any code change to the integration.
- **Entity names** come from `translation_key` + translation files.
- **Tests** run with `python3 -m unittest discover -s tests` (96 tests, <1ms).
- **CRC status** — see `docs/crc_analysis.md` for full analysis:
  - NOT any standard CRC-32 (all 2^32 polynomials tested, all configs)
  - IS linear (proven: XOR delta consistency, 171 pairs, 60 groups)
  - Contributions follow doubling pattern: C[b]_LE = 0x01d8ac87 << b
  - Can predict CRC for any byte[15] within same frame structure (19/19)
  - GF(2) GCD = degree 40 (temp-only), but degree 0 when mixing sessions
  - Conclusion: session-dependent state prevents cross-session polynomial extraction
  - **To crack fully**: capture ALL command types in ONE session, re-run
    `tools/crack_crc.py`. Or disassemble PB554 firmware.
  - CRC analysis scripts: `tools/analyze_crc.py`, `tools/crack_crc.py`, `tools/debug_crc.py`
- **Temperature lookup table**: `tools/captures_temp/temp_commands.json`
  - 31 frames covering 10°C (50°F) to 40°C (104°F) in 1°C steps
  - °F pattern: +1,+2,+2,+2,+2 repeating (1°C ≈ 1.8°F)
  - 73°F (23°C) frame has escaped byte in CRC (0x1B→0x1B0B on wire, 23 bytes raw)
  - Byte 11 varies by capture session: 0x88, 0x98, 0x99 — likely encodes
    system state at capture time (pump/heater). Needs live testing to confirm
    whether replaying works regardless.
  - Stored as raw wire hex; replay sends verbatim (including escapes)
  - Keys are `"NNF"` (e.g. `"73F"`) → hex frame string
- **Temperature capture script**: `tools/capture_temp_commands.py` (v2.1)
  - Supports `--button up/down`, `--steps N`, shows missing temps on startup
  - Handles pseudo-escaped frames (unescape before length check)
  - Output dir defaults to `tools/captures_temp/` (relative to script)
- **Command send pattern**: coordinator opens TCP, writes frame, closes.
  Uses `asyncio.Lock` to prevent concurrent sends.
- **Pump state machine**: must follow OFF→low→high→OFF cycle.
  "Pump off" button handles low→high→off with 1s delay between frames.
- **Light is a toggle**: same frame for on and off. Switch entity checks
  current state before sending to avoid double-toggle.
