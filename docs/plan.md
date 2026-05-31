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
> **Status:** All 8 write tests pass. Community safety fixes applied (session 7).
> Ozone mode broadcast byte discovered. Ready for live ozone test.

> **Documentation policy:** `docs/protocol.md` is the canonical protocol spec.
> This `docs/plan.md` is progress/handoff only.

## 0. AI Instructions

- **No PII / timestamps in code.** Do NOT add dates, author names, usernames,
  IP addresses, or any data that could identify the developer or when work was
  done. Dates belong only in this plan file and in git history — never in
  `.py`, `.json`, or other shipped files.
- **Naming convention for data keys and entities.** Keep names short and
  consistent. No `_state` or `_status` suffixes — use bare nouns: `jets`,
  `blower`, `light`, `status`, `setpoint`. The integration is pre-release;
  there is no backwards compatibility constraint on key/entity naming.
  When in doubt, match the naming already used by sibling entities.
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
- **Light:** RGB LED, 9 states cycling via panel button; RS485 is simple on/off toggle
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone port:** Connector on PCB ("Ozonauslass"). PB554 has two modes:
  **Auto** (schedule) and **Manual** (user-triggerable via RS485).
- **Blower:** air blower, connector on PCB, button on PB554 panel.

## 2. Protocol Summary

All protocol details—including framing, byte maps, command payloads, schedule encoding, and the verified CRC-32 algorithm—have been moved to `docs/protocol.md`, which is the canonical protocol reference.

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
├── switch.py            # light, heater, blower, ozone, schedule slot enables
├── fan.py               # jets (off/low/high via preset_modes)
├── climate.py           # thermostat with debounced slider
├── time.py              # schedule time slot start/end (8 entities, read+write)
├── button.py            # sync spa clock to HA time
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), dynamic command builders
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Entities

| Entity | Platform | Key | What it does |
|--------|----------|-----|--------------|
| **Water temperature** | sensor | `water_temperature` | Integer °C for history/graphs |
| **Setpoint** | sensor | `setpoint` | Current target temperature °C |
| **Status** | sensor | `status` | Enum: off / circulation / heating / ozone / unknown; dynamic icon per state |
| **Jets** (Düsen) | sensor | `jets` | Enum: off / low / high |
| **Thermostat** | climate | `thermostat` | Water temp + setpoint + status; slider with 1.5s debounce |
| **Heater** | switch | `heater` | On/off; dynamic command via CRC |
| **Ozone** | switch | `ozone` | Manual on/off (only visible when mode=Manual) |
| **Light** | switch | `light` | On/off via toggle (state guard: refuses when unknown) |
| **Blower** | switch | `blower` | On/off; dynamic command via CRC; byte[28] bit 3 = state |
| **Heat slot 1 / 2** | switch | `heat_slot{n}_enabled` | Enable/disable heat schedule slots |
| **Filter slot 1 / 2** | switch | `filter_slot{n}_enabled` | Enable/disable filter schedule slots |
| **Jets** (Düsen) | fan | `jets` | Off/low/high via preset_modes; sends target state directly |
| **Heat slot 1/2 start/end** | time | `heat_slot{n}_{start\|end}` | Read+write heat schedule times (HH:MM) |
| **Filter slot 1/2 start/end** | time | `filter_slot{n}_{start\|end}` | Read+write filter schedule times (HH:MM) |
| Sync clock | button | `sync_clock` | Sends current HA time to spa controller (disabled by default) |
| RS485 bridge | binary_sensor | `bridge_connectivity` | TCP connectivity (disabled by default) |
| Spa clock | sensor | `spa_datetime` | Diagnostic timestamp (disabled by default) |

### Key design decisions

> Protocol byte-level details (command payloads, CRC, byte maps) are in
> [`docs/protocol.md`](protocol.md). This section covers implementation choices only.

- **All commands built dynamically** — no replay-only frames.
  `_build_button_command()` is the universal builder for type-0xA1 commands.
- **Temperature setpoint**: `btn_action=0x98` confirmed working via live test.
- **Pump commands** — target-state based (not transition-based). Controller
  accepts `off=(0x04,0x00)`, `low=(0x02,0x02)`, `high=(0x06,0x04)` regardless
  of current state.
- **Ozone control** — mode (Auto/Manual) set via options flow, synced to spa.
  Switch only sends manual ON/OFF. Mode readable from broadcast byte 13 bit 7.
- **Ozone mode sync** — coordinator reads byte 13 from broadcast; if it differs
  from config option, updates the option to match. Options flow sends mode
  command when user changes the setting.
- **Fan entity retry logic** — pump commands retry up to 3× with state check.
  RS485 bus collisions can cause commands to be lost.
- **Light toggle** — `_send_toggle()` has 1.0s delay before refresh.
- **Heater byte blower-flag fix** — `parse_status()` strips blower bit via
  `heater_base = heater_byte & ~MASK_HEATER_BLOWER` before status lookup.
- **Climate debounce**: 1.5s coalescing for slider drags.
- **Coordinator write pacing**: global 1.0s command cooldown.
- **Temperatures as integers** — spa only shows whole °C.
- **Schedule** — `time` entities for pickers, `switch` entities for enables.
  Schedule sends REFUSE if any data key is missing (prevents overwrite with zeros).
- **Clock write** — uses `set_date=True` (prefix=0x05) by default.
- **Options flow** — ozone mode (Auto/Manual) and auto clock sync (bool, default OFF).
- **Auto clock sync** — disabled by default. When enabled, syncs if drift > 30s
  with 1-hour cooldown.
- **No auto commands on startup** — all writes are user-initiated only.
- **Consistent logging** — all write entities log at debug before send and
  error on failure, using `"Entity: action"` format.

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1–6 | ✅ Done | Capture, integration, byte map, writes, temp control |
| 7. Live test writes | ✅ Done | All 8 tests pass |
| 8–16 | ✅ Done | Schedule, CRC, DateTime, ozone, dynamic commands |
| 17. Options flow | ✅ Done | Ozone mode + auto clock sync |
| 18. Safety fixes | ✅ Done | Session 7: no auto writes, schedule guard, pump simplification |
| 19. Polish & release | **Next** | Live ozone test, version bump, HACS release |

## 5. Next Steps

### Priority 1: Remaining live tests
1. **Test ozone** — untested live (ozone mode byte 13 detection confirmed from captures)
2. **Verify auto clock sync** — check logs for "Spa clock drift" messages

### Priority 2: Polish & release
- UI feedback delay: consider optimistic state updates
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session 7 outcomes (2026-05-28/29):**
  - **Safety fixes applied** — no auto-commands on startup (removed
    `async_apply_ozone_mode()`), auto clock sync default OFF, schedule
    overwrite guard (refuses to send if data keys missing).
  - **Pump simplified** — target-state-only commands (no transition map).
    `build_pump_command(target)` API. Controller accepts any target directly.
  - **Ozone redesigned** — mode set via options flow (sends command to spa),
    switch is just manual ON/OFF. No two-step in the switch.
  - **Ozone mode broadcast byte FOUND** — byte 13 bit 7: 0=Auto, 1=Manual.
    Confirmed from phase 6 captures (files 52-57). Previously thought to be
    "no broadcast change" — wrong. The mode IS always readable.
  - **Coordinator auto-syncs config option** with broadcast byte 13 value.
    On first install, first broadcast immediately tells the correct mode.
  - **Consistent logging** added to all write entities.

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests**: `source .venv/bin/activate && pytest -q` → `76 passed, 2 skipped`.
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
