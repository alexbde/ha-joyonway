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
> **Status:** Phase 6 capture and analysis complete. Schedule flags byte cracked
> and implemented. Ozone commands captured (entity not yet built). All schedule
> enable/disable logic uses proper flags byte. Live write testing next.

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
- **Light:** RGB LED, 9 states cycling via button
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone port:** Connector on PCB ("Ozonauslass"), byte 14=`0xC1` is the
  disinfection cycle state. PB554 has two modes: **Auto** (schedule) and
  **Manual** (user-triggerable). Command frames captured for mode switch
  and manual ON/OFF (Phase 6). Broadcast: heater byte `0x40`↔`0xC1`.
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

| Entity | Platform | Key | What it does |
|--------|----------|-----|--------------|
| **Water temperature** | sensor | `water_temperature` | Integer °C for history/graphs |
| **Setpoint** | sensor | `setpoint` | Current target temperature °C |
| **Status** | sensor | `status` | Enum: off / circulation / heating / disinfection / unknown; dynamic icon per state |
| **Jets** (Düsen) | sensor | `jets` | Enum: off / low / high |
| **Thermostat** | climate | `thermostat` | Water temp + setpoint + status; slider with 1.5s debounce |
| **Heater** | switch | `heater` | On/off via distinct replay frames |
| **Filtration** | switch | `filter` | On/off; pump low = filtration running |
| **Light** | switch | `light` | On/off via toggle replay (state guard: refuses when unknown) |
| **Blower** | switch | `blower` | On/off via distinct replay frames; byte[28] bit 3 = state |
| **Heat slot 1 / 2** | switch | `heat_slot{n}_enabled` | Enable/disable heat schedule slots |
| **Filter slot 1 / 2** | switch | `filter_slot{n}_enabled` | Enable/disable filter schedule slots |
| **Jets** (Düsen) | fan | `jets` | Off/low/high via preset_modes; handles multi-step transitions |
| **Heat slot 1/2 start/end** | time | `heat_slot{n}_{start\|end}` | Read+write heat schedule times (HH:MM) |
| **Filter slot 1/2 start/end** | time | `filter_slot{n}_{start\|end}` | Read+write filter schedule times (HH:MM) |
| Sync clock | button | `sync_clock` | Sends current HA time to spa controller (disabled by default) |
| RS485 bridge | binary_sensor | `bridge_connectivity` | TCP connectivity (disabled by default) |
| Spa clock | sensor | `spa_datetime` | Diagnostic timestamp (disabled by default) |

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
- **Schedule enable/disable**: flags byte (byte 7) encodes slot enables via lookup
  table: `0xAA`=both on, `0x62`=s1 on/s2 off, `0x9A`=s1 off/s2 on, `0x52`=both off.
  Implementation uses `SCHED_FLAGS_TABLE` in p25b85.py. ✅ Done.

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1–6 | ✅ Done | Capture, integration, byte map, writes, temp control |
| 7. Live test writes | **Next** | Test all write entities at spa |
| 8. Schedule entities | ✅ Done | `time` + `switch` entities with dynamic CRC write |
| 9. CRC cracking | ✅ Done | P=0x04C11DB7, word32-swap, verified 44/44 frames |
| 10. DateTime sync | ✅ Done | `button` entity, verified against 2 captured frames |
| 11. Phase 6 capture | ✅ Done | Full functionality capture: all entities + ozone + panel-local |
| 12. Schedule flags | ✅ Done | Flags byte = lookup table, implemented + tested |
| 13. Ozone commands | ✅ Done | Mode Auto/Manual + manual ON/OFF frames captured |
| 14. Panel-local | ✅ Done | Auto lock, brightness, screen flip confirmed panel-local |
| 15. Polish & release | Planned | After live test |

## 5. Next Steps

### Priority 1: Live testing
1. **Run `tools/guided_write_test.py`** — round-trip write tests (direct TCP, no HA)
2. **Restart HA** with updated integration
3. **Test each entity via HA UI**: light, heater, filter, blower, jets, thermostat, schedule times
4. **Verify schedule writes**: change a time slot, confirm broadcast updates
5. **Verify schedule enable/disable**: toggle a slot switch, check broadcast

### Priority 2: Replace temperature lookup table
- `TEMP_COMMAND_TABLE` (31 entries) can be replaced with `build_frame()`
- Byte 10 variants (0x80/0x98/0x99) need live test to confirm which works
- Would allow ANY °F setpoint, not just the 31 captured values

### Priority 3: Automatic clock sync
- Currently manual via a button (disabled by default).
- **Idea:** Add a config flow option "Auto-sync clock" (boolean, default ON).
  When enabled, the coordinator compares `spa_datetime` to HA time on each
  broadcast and sends a DateTime command if drift exceeds a threshold (e.g. 30s).
- Could also be a configurable interval (e.g. daily at 03:00) via HA automation,
  but a built-in option is more user-friendly.
- Keep the manual button as a fallback (disabled by default).

### Priority 4: Ozone / Disinfection entity
- Implement the two-step ozone control via an entity.

### Priority 5: Polish & release
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session outcomes (latest — 2026-05-27):**
  - **Phase 6 full capture completed**
  - **Schedule flags byte CRACKED:** byte[7] encodes slot enables via lookup table.
  - **Ozone commands captured:**
    - Broadcast: `0x40` → `0xC1` (disinfection on), `0xC1` → `0x40` (off)
  - **Panel-local confirmed:** Auto Lock, Brightness, Screen Flip = no RS485
  - **New command frame variants** for heater/blower/pump captured.

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests now run with pytest**:
  - Lightweight mode (no HA runtime):
    - `source .venv/bin/activate && pytest -q`
    - Current result: `67 passed, 2 skipped`.
  - HA runtime mode:
    - `source .venv-ha/bin/activate && pytest -q`
    - `.venv-ha` has `python3.12` + `homeassistant` + `pytest-homeassistant-custom-component`.
- **Protocol docs**: `docs/protocol.md` — full protocol reference, updated with
  Phase 6 findings (schedule flags, ozone, heater byte values, panel-local).
- **Schedule command generation**: `build_schedule_command()` in `adapters/p25b85.py`
  now accepts `slot1_enabled`/`slot2_enabled` params and uses `SCHED_FLAGS_TABLE`
  lookup. Verified against Phase 6 captures (byte-for-byte match).
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
- **Tools**: `guided_write_test.py` (live write tests), `guided_capture_phase6.py` (capture),
  `analyze_phase6.py` (capture analysis), `show_layout.py` (dashboard preview),
  `read_schedule_datetime.py`, `dump_broadcast_bytes.py`, capture/analysis tools in `tools/`
