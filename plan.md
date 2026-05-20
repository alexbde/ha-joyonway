# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller,
> with a model adapter interface ready for future multi-model expansion.
>
> **Repo:** `alexbde/ha-joyonway-p25b85` — independent from upstream
> **Upstream:** christopheknap keeps `ha-joyonway-p23b32` P23B32-only.
> His code remains at https://github.com/KnapTheBuilder/ha-joyonway-p23b32.
>
> **Integration domain:** `joyonway_p25b85`
> **Hardware:** P25B85 + PB554 + Elfin EW11
> **Status:** All entities implemented. Needs live testing at spa.

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
- **Ozone port:** Connector exists on PCB ("Ozonauslass"), but byte 14=0x41
  is actually a **scheduled filtration cycle** state (not a separate UV device)
- **Blower:** connector exists but NOT wired

---

## 2. Protocol Summary

### Framing

- 38400 8N1, start `0x1A`, end `0x1D`
- Pseudo-escape: `0x1B XX` sequences (see escape table in code)
- P25B85: full-frame unescape. P23B32: tail-only (bytes 55+).
- Frame boundaries detected on raw bytes FIRST, then unescape applied.

### Broadcast byte map (P25B85, logical frame after unescape)

| Byte | Content |
|------|---------|
| 8 | Model ID (`0x03` = P25B85) |
| **9** | Water temperature (°F) |
| **12** | Pump status (`0x02`=low, `0x04`=high) |
| **14** | Heater state (`0x40`=cooldown, `0x50`=circulation, `0x55`=heating, `0x41`=filtration cycle) |
| **16** | Setpoint (°F) |
| **17** | Light flags (bit 0 = light ON) |
| 53–58 | Date/time (year, month, day, hour, minute, second) |

### Captured command frames

| Action | Frame (hex) |
|--------|-------------|
| Light toggle | `1a0120103ca110a10000404000c00056003031eeb21d` |
| Pump OFF→low | `1a0120103ca110a10202000000c00056007dd2146b1d` |
| Pump low→high | `1a0120103ca110a10604000000c0005600fc1221c61d` |
| Pump high→OFF | `1a0120103ca110a10400000000c0005600735738e91d` |

Temperature: 31 frames in `TEMP_COMMAND_TABLE` (adapters/p25b85.py), 10-40°C.

### CRC safety

- ❌ NEVER send frames with forged CRC (can activate heater — KDy warning)
- ✅ ONLY replay verbatim captured frames from physical panel
- See `docs/crc_analysis.md` for full analysis

---

## 3. Current Implementation

### File structure

```
custom_components/joyonway_p25b85/
├── __init__.py          # entry setup, coordinator creation
├── const.py             # domain, config keys, PLATFORMS
├── manifest.json        # HACS-compatible, v0.1.0
├── config_flow.py       # IP + port, TCP connection test
├── protocol.py          # find_frames, pseudo_unescape, validate_frame
├── coordinator.py       # async TCP polling + async_send_command
├── sensor.py            # adapter-driven (water temp + diagnostics)
├── binary_sensor.py     # bridge connectivity only
├── switch.py            # light toggle (on/off via replay)
├── fan.py               # pump (off/low/high via preset_modes)
├── climate.py           # thermostat with debounced slider
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), command frames, temp table
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Entities (final, clean)

| Entity | Platform | What it does |
|--------|----------|--------------|
| **Thermostat** | climate | Water temp + setpoint + heater state (HEATING/PREHEATING/IDLE); slider with 1.5s debounce; extra attribute `heater_state` |
| **Light** | switch | On/off via toggle replay |
| **Pump** | fan | Off/low/high via preset_modes; handles multi-step transitions |
| **Water temperature** | sensor | Integer °C for history/graphs |
| **RS485 bridge** | binary_sensor | TCP connectivity |
| Spa clock | sensor | Diagnostic (disabled by default) |
| Raw pump byte | sensor | Diagnostic (disabled by default) |
| Raw heater byte | sensor | Diagnostic (disabled by default) |

### PLATFORMS in const.py

`["sensor", "binary_sensor", "switch", "fan", "climate"]`

### Key design decisions

- **No `button.py`** — pump control is now the fan entity (deleted button.py)
- **No UV/ozone binary sensor** — byte 14=0x41 is "scheduled filtration cycle",
  not a separate device. Info available via climate's `heater_state` extra attribute.
- **No light binary sensor** — redundant with light switch (switch shows state)
- **No pump binary sensors** — redundant with fan entity's preset_mode
- **No heater_state sensor** — integrated into climate's hvac_action + extra attribute
- **No setpoint sensor** — shown by climate entity's target_temperature
- **Temperatures as integers** — spa only displays whole °C; `_fahrenheit_to_celsius()` returns `int`
- **Climate slider debounce** — 1.5s delay before sending; prevents RS485 flooding when dragging slider
- **Climate hvac_action mapping**: heating→HEATING, circulation→PREHEATING, cooldown/filtration→IDLE
- **Fan preset_modes**: "low" (filtration), "high" (jets); handles all state transitions including multi-step

---

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Capture tools | ✅ Done | `guided_capture_38400.py`, `frame_parser_38400.py` |
| 2. Integration | ✅ Done | Deployed, reading live data, HACS install works |
| 3. Validate byte map | ✅ Done | All byte positions confirmed from captures |
| 4. Write commands | ✅ Done | Light + pump replay frames captured |
| 5. Live test writes | **Next** | Test all write entities at spa |
| 6. Temperature control | ✅ Done | Climate with debounced slider, 31-frame lookup |
| 7. Polish & release | Planned | After Phase 5 live test |

---

## 5. Next Steps

1. **Live test at spa** — restart HA, test light switch, pump fan, thermostat slider
2. **Polish** — version bump, README update, HACS release
3. **PR to frame-analyzer** — add P25B85 preset to christopheknap's tool

---

## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests** run with `python3 -m unittest discover -s tests` (96 tests, <1ms).
- **Temperature lookup table**: `TEMP_COMMAND_TABLE` in `adapters/p25b85.py`
  - 31 frames covering 10°C (50°F) to 40°C (104°F)
  - 73°F (23°C) frame has escaped byte in CRC (23 bytes raw vs 22 normal)
  - Byte 11 varies by capture session (0x88/0x98/0x99) — needs live test
  - Stored as raw wire hex; replay sends verbatim (including escapes)
- **Command send pattern**: coordinator opens TCP, writes frame, closes.
  Uses `asyncio.Lock` to prevent concurrent sends.
- **Pump state machine**: must follow OFF→low→high→OFF cycle.
  Fan entity handles multi-step transitions (e.g., low→off requires low→high then high→off with 1s delay).
- **Light is a toggle**: same frame for on and off. Switch entity checks
  current state before sending to avoid double-toggle.
- **Climate debounce**: `TEMP_DEBOUNCE_SECONDS = 1.5` — slider calls are
  coalesced; only the final value is sent after the slider settles.
- **CRC** — see `docs/crc_analysis.md`. Linear but session-dependent.
  To crack: capture ALL command types in ONE session, or disassemble PB554 firmware.
