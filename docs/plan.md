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
> **Status:** Schedule entities (time + switch) and DateTime sync (button)
> implemented with dynamic CRC write support. Needs live testing at spa.

> **Documentation policy:** `docs/protocol.md` is the canonical protocol spec.
> This `docs/plan.md` is progress/handoff only.

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
  3. Review `README.md` and update it if implementation, entities, terminology,
     setup steps, or safety notes changed during the session.
  4. Verify the plan file is self-contained — a new AI session with no
     prior context should be able to read it and continue the project.

## 1. Hardware

- **Spa:** Home Deluxe White Marble (outdoor whirlpool, rigid/hardshell)
- **Controller:** Joyonway P25B85, PCB `P2325B0003 R05`
- **Touchpad:** PB554 colour screen
- **Bridge:** Elfin EW11, RS-485 → WiFi, TCP server (IP in `.env`, port 8899)
  - Supports **4 simultaneous TCP connections** (tested: 3 new + HA = 4)
  - All connections receive the **same full RS485 data stream** (multicast)
- **UART:** 38400 8N1
- **Pump:** ONE dual-speed (low = filtration, high = massage jets, 20-min auto-off)
- **Light:** RGB LED, 9 states cycling via button
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone port:** Connector on PCB ("Ozonauslass"), byte 14=0x41 is the
  disinfection cycle state. PB554 manual confirms two modes: **Auto** (schedule)
  and **Manual** (user-triggerable from panel). Manual mode adds an ozone icon
  to the panel's function screen. Command frame for manual toggle not yet captured.
- **Blower:** air blower, connector on PCB, button on PB554 panel.

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
| **14** | Heater/blower state (see below) |
| **16** | Setpoint (°F) |
| **17** | Light flags (bit 0 = light ON) |
| **19–26** | Heat schedule (4 pairs: start_h, start_m, end_h, end_m per slot) |
| **28** | Activity flags (bit 3=blower, bit 5=activity/disinfection) |
| **29–36** | Filter schedule (same layout as heat) |
| 53–58 | Date/time (year, month, day, hour, minute, second) |

**Schedule encoding:** Start-hour bytes (19, 23, 29, 33) use bit 6 (0x40) as
slot-enabled flag. Hour = byte & 0x3F. Minutes are in the next byte.

**Byte 14 values:**
- `0x40` = off, `0x50` = circulation, `0x55`/`0x54` = heating
- `0x41`/`0xC1` = disinfection, `0x58` = blower active (0x50 + bit 3)

### Command frame types (byte[4] distinguishes type)

| byte[4] | Type | Description |
|---------|------|-------------|
| 0xA1 | Button command | Light/pump/heater/blower (22 bytes) |
| 0xA2 | DateTime set | Set spa clock (22 bytes) |
| 0xA3 | Heat schedule | Program heating time slots (22 bytes) |
| 0xA4 | Filter schedule | Program filtration time slots (22 bytes) |

### Schedule command payload (0xA3 / 0xA4)

```
[0-6]  Header: 01 20 10 3C [A3|A4] 10 A1
[7]    Flags: 0x62 (heat) / 0xAA (filter) — static observed value
[8-9]  Slot 1 start: hour, minute
[10-11] Slot 1 end: hour, minute
[12-13] Slot 2 start: hour, minute
[14-15] Slot 2 end: hour, minute
```

**Verified:** `build_schedule_command("heat", (12,0), (16,0), (20,0), (22,0))`
produces byte-for-byte identical frame to captured session 2 heat schedule.

### DateTime command payload (0xA2)

```
[0-6]  Header: 01 20 10 3C A2 10 A1
[7]    0x50 (fixed prefix)
[8]    Year (offset from 2000)
[9]    Month
[10]   Day
[11]   Hour (24h)
[12]   Minute
[13]   Second
[14-15] 0x00 0x00
```

### CRC — CRACKED ✅

- **Algorithm:** CRC-32 (0x04C11DB7), non-reflected, init=0, xor_out=0x552D22C8
- **Preprocessing:** 32-bit word byte-swap of payload before CRC
- **Storage:** little-endian at payload bytes 16–19
- **Implementation:** `protocol.py` → `compute_crc()` and `build_frame()`
- **Verification:** 21/21 unique same-session frames, all command types

## 3. Current Implementation

### File structure

```
custom_components/joyonway_p25b85/
├── __init__.py          # entry setup, coordinator creation
├── const.py             # domain, config keys, PLATFORMS
├── manifest.json        # HACS-compatible, v0.1.0
├── config_flow.py       # IP + port, TCP connection test
├── protocol.py          # framing, unescape, CRC-32, build_frame
├── coordinator.py       # async TCP polling + async_send_command
├── sensor.py            # adapter-driven (water temp, heater/pump state, diagnostics)
├── binary_sensor.py     # bridge connectivity only
├── switch.py            # light, heater, blower, schedule slot enables
├── fan.py               # jets (off/low/high via preset_modes)
├── climate.py           # thermostat with debounced slider
├── time.py              # schedule time slot start/end (8 entities, read+write)
├── button.py            # sync spa clock to HA time
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), command frames, schedule builder
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Entities

| Entity | Platform | What it does |
|--------|----------|--------------|
| **Thermostat** | climate | Water temp + setpoint + heater state; slider with 1.5s debounce |
| **Light** | switch | On/off via toggle replay (state guard: refuses when unknown) |
| **Heater** | switch | On/off via distinct replay frames |
| **Blower** | switch | On/off via distinct replay frames; byte[28] bit 3 = state |
| **Heat slot 1 / 2** | switch | Enable/disable heat schedule slots |
| **Filter slot 1 / 2** | switch | Enable/disable filter schedule slots |
| **Jets** (Düsen) | fan | Off/low/high via preset_modes; handles multi-step transitions |
| **Heat slot 1/2 start/end** | time | Read+write heat schedule times (HH:MM) |
| **Filter slot 1/2 start/end** | time | Read+write filter schedule times (HH:MM) |
| **Sync clock** | button | Sends current HA time to spa controller |
| **Water temperature** | sensor | Integer °C for history/graphs |
| **Heater state** | sensor | Enum: off / circulation / heating / disinfection / unknown |
| **Pump state** | sensor | Enum: off / low / high |
| **RS485 bridge** | binary_sensor | TCP connectivity |
| Spa clock | sensor | Diagnostic timestamp (disabled by default) |
| Raw pump byte | sensor | Diagnostic (disabled by default) |
| Raw heater byte | sensor | Diagnostic (disabled by default) |

### Key design decisions

- **Fan = "Jets" / "Düsen"** — matches spa manual terminology
- **Light toggle safety**: same frame for on/off; switch refuses toggle when state is unknown
- **Heater/blower switches**: distinct ON/OFF frames (not toggles); safe to send
- **Climate debounce**: 1.5s coalescing for slider drags
- **Coordinator write pacing**: global 1.0s command cooldown
- **Pump state machine**: OFF→low→high→OFF cycle; fan handles multi-step transitions
- **Temperatures as integers** — spa only shows whole °C
- **Schedule times as `time` entities** — proper HA time pickers, supports HH:MM
- **Schedule enables as `switch` entities** — toggle slots on/off
- **Schedule write**: builds full command with all 4 slot values + CRC via `build_frame()`
- **Schedule disable mechanism**: sends 00:00–00:00 for disabled slot (needs live verification)

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1–6 | ✅ Done | Capture, integration, byte map, writes, temp control |
| 7. Live test writes | **Next** | Test all write entities at spa |
| 8. Schedule entities | ✅ Done | `time` + `switch` entities with dynamic CRC write |
| 9. CRC cracking | ✅ Done | P=0x04C11DB7, word32-swap, verified 21/21 frames |
| 10. DateTime sync | ✅ Done | `button` entity, verified against 2 captured frames |
| 11. Polish & release | Planned | After live test |

## 5. Next Steps

### Priority 1: Live testing
1. **Restart HA** with updated integration
2. **Test each entity**: light, heater, blower, jets, thermostat, schedule times, sync clock
3. **Verify schedule writes**: change a time slot, confirm broadcast updates
4. **Verify schedule enable/disable**: toggle a slot switch, check broadcast
5. **Verify clock sync**: press button, check spa_datetime sensor updates
6. **Check schedule flags byte**: 0x62 (heat) and 0xAA (filter) may need
   adjustment if controller rejects commands — could encode enable states

### Priority 2: Replace temperature lookup table
- `TEMP_COMMAND_TABLE` (31 entries) can be replaced with `build_frame()`
- Byte 10 variants (0x80/0x98/0x99) need live test to confirm which works
- Would allow ANY °F setpoint, not just the 31 captured values

### Priority 3: Ozone manual control
- PB554 manual confirms ozone has Auto + Manual modes (screenshot from manual)
- In Manual mode, an ozone icon appears on panel and user can toggle it
- **Need to capture**: set panel to "Ozone Mode: M", then capture the toggle command
- Likely a button command (0xA1) with a new byte[9]/byte[10] pair
- Broadcast state: byte 14 = 0x41/0xC1 when disinfection active

### Priority 4: Remaining PB554 config options (uncaptured)
From the spa manual, the PB554 "Set" menu has these options not yet implemented:
- **Ozone Mode** (Auto/Manual) — config command to switch modes, likely a new
  command type or a settings frame. Determines whether ozone icon appears.
- **Auto Lock** (On/Off) — panel auto-lock after timeout. May be panel-local
  (not sent on RS485) similar to screen flip. Needs testing.
- **Brightness** — display brightness. Likely panel-local (not on RS485),
  similar to screen flip.
- **Light mode** (On-Off / RGB cycling) — PB554 manual describes two light modes:
  simple on/off or 9-state RGB cycling. The mode selection might be a config
  command or panel-local.
- **Heater priority** — heater or hydromassage priority (can't run both if
  breaker too small). This is a **DIP switch** setting on the controller PCB
  (A2/A3/A5 on the steuerbox), NOT an RS485 command. Not applicable.
- **Frost protection** — controller has built-in frost protection mode.
  Not accessible from PB554 panel. Likely automatic based on temp sensor.

**Probably NOT on RS485 (panel-local):** Auto Lock, Brightness, Screen flip.
**Worth capturing:** Ozone Mode toggle, Light mode config.
**Hardware config (DIP switches):** A1 host/slave, A2/A3/A5 function config.
**Not planned:** "Modes" config options (panel economy/standard/boost presets) —
these are composite shortcuts that combine setpoint + schedule changes.
Integration users can achieve the same via HA automations/scenes instead.

### Priority 5: Polish & release
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests now run with pytest**:
  - Lightweight mode (no HA runtime):
    - `source .venv/bin/activate && pytest -q`
    - Current result: `64 passed, 2 skipped`.
  - HA runtime mode:
    - `source .venv-ha/bin/activate && pytest -q`
    - `.venv-ha` has `python3.12` + `homeassistant` + `pytest-homeassistant-custom-component`.
- **Protocol docs**: `docs/protocol.md` — full protocol reference with schedule
  broadcast encoding section (6b), CRC algorithm, all captured frame examples.
- **Schedule command generation verified**: `build_schedule_command()` in
  `adapters/p25b85.py` produces byte-for-byte match with captured frames.
- **Schedule flags byte**: 0x62 (heat) / 0xAA (filter) are static in our captures.
  Unclear if they encode enable state or are mode identifiers. Live test needed.
- **Schedule slot disable**: currently implemented by sending 00:00–00:00 times.
  May need different approach if controller doesn't accept zero times. Alternative:
  the flags byte might control enables (needs live experimentation).
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
- **Tools added this session**: `test_ew11_max_connections.py`,
  `test_ew11_dual_stream.py`, `read_schedule_datetime.py`, `dump_broadcast_bytes.py`
