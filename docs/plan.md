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
> **Status:** All command frames built dynamically via CRC. Ozone entity
> implemented with two-step control. All entities functional. Live testing next.

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
  ozone cycle state. PB554 has two modes: **Auto** (schedule) and
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
| **Ozone** | switch | `ozone` | Ozone on/off; two-step: mode→Manual + manual ON/OFF |
| **Light** | switch | `light` | On/off via toggle (state guard: refuses when unknown) |
| **Blower** | switch | `blower` | On/off; dynamic command via CRC; byte[28] bit 3 = state |
| **Heat slot 1 / 2** | switch | `heat_slot{n}_enabled` | Enable/disable heat schedule slots |
| **Filter slot 1 / 2** | switch | `filter_slot{n}_enabled` | Enable/disable filter schedule slots |
| **Jets** (Düsen) | fan | `jets` | Off/low/high via preset_modes; handles multi-step transitions |
| **Heat slot 1/2 start/end** | time | `heat_slot{n}_{start\|end}` | Read+write heat schedule times (HH:MM) |
| **Filter slot 1/2 start/end** | time | `filter_slot{n}_{start\|end}` | Read+write filter schedule times (HH:MM) |
| Sync clock | button | `sync_clock` | Sends current HA time to spa controller (disabled by default) |
| RS485 bridge | binary_sensor | `bridge_connectivity` | TCP connectivity (disabled by default) |
| Spa clock | sensor | `spa_datetime` | Diagnostic timestamp (disabled by default) |

### Key design decisions

- **Terminology: "Ozone"** — matches the hardware manual ("Ozonauslass") and community
  usage. Ozone is distinct from filtration: the ozone/UV port is a separate device that
  forces the filter pump on when active. Data key `ozone_active`, status enum
  `"ozone"`, constants `HEATER_OZONE` / `HEATER_OZONE_ALT` — all consistent.
- **All commands built dynamically** — CRC-32 cracked (P=0x04C11DB7, word32-swap);
  no replay-only frames. `_build_button_command()` is the universal builder for
  type-0xA1 commands (light, heater, blower, pump, temp, ozone).
- **Ozone two-step control** — ON: send mode→Manual, delay 1.5s, send manual ON.
  OFF: send manual OFF, delay 1.5s, send mode→Auto. Broadcast byte 14 tracks state.
- **Fan = "Jets" / "Düsen"** — matches spa manual terminology
- **Light toggle safety**: same frame for on/off; switch refuses toggle when state is unknown
- **Heater/blower switches**: distinct ON/OFF commands; safe to send
- **Climate debounce**: 1.5s coalescing for slider drags
- **Coordinator write pacing**: global 1.0s command cooldown
- **Pump state machine**: OFF→low→high→OFF cycle; fan handles multi-step transitions
- **Temperatures as integers** — spa only shows whole °C
- **Schedule times as `time` entities** — proper HA time pickers, supports HH:MM
- **Schedule enables as `switch` entities** — toggle slots on/off
- **Schedule write**: builds full command with all 4 slot values + CRC via `build_frame()`
- **Schedule enable/disable**: flags byte (byte 7) encodes slot enables via lookup
  table: `0xAA`=both on, `0x62`=s1 on/s2 off, `0x9A`=s1 off/s2 on, `0x52`=both off.

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
| 15. Dynamic commands | ✅ Done | All commands built via CRC; replay table removed |
| 16. Ozone entity | ✅ Done | Two-step switch; verified against Phase 6 captures |
| 17. Polish & release | Planned | After live test |

## 5. Next Steps

### Priority 1: Live testing
1. **Run `tools/guided_write_test.py`** — round-trip write tests (direct TCP, no HA)
2. **Restart HA** with updated integration
3. **Test each entity via HA UI**: light, heater, ozone, blower, jets, thermostat, schedule times
4. **Verify schedule writes**: change a time slot, confirm broadcast updates
5. **Verify schedule enable/disable**: toggle a slot switch, check broadcast
6. **Verify ozone**: toggle ozone switch, confirm broadcast byte 14 changes
7. **Verify dynamic temp commands**: check if byte 10 = 0x88 is accepted by controller

### Priority 2: Options flow for spa modes
The PB554 panel has deeper settings that control whether certain features are
accessible via RS485 at all. Exposing them as integration options would improve UX.

**Planned options** (via HA options flow, changeable after setup):

| Option | Type | Default | Effect |
|--------|------|---------|--------|
| Ozone mode | select: Auto / Manual | Auto | Auto = schedule-only (hide ozone switch). Manual = enable ozone switch for RS485 control. Sends mode command on change. |
| Auto-sync clock | bool | ON | Coordinator auto-syncs spa clock when drift > 30s |

**Design rationale:**
- **Ozone mode**: When mode is Auto, the ozone cycle runs on its fixed schedule
  and the manual ON/OFF commands are ignored by the controller. Showing the ozone
  switch in this state is misleading — it would appear to do nothing. When the user
  sets mode to Manual via options, the integration sends the mode-switch command
  and exposes the ozone switch. The switch entity would be dynamically
  enabled/disabled based on this option.
- **Heater manual button**: The PB554 panel has a "show manual heating button"
  option. This is likely panel-local (Phase 14 confirmed panel settings don't
  produce RS485 frames). The heater switch already works via RS485 regardless
  of this panel setting, so no integration option is needed.
- **Implementation approach**: Add `OptionsFlow` to `config_flow.py`. Store
  options in `entry.options`. Coordinator reads options on startup and on
  `async_options_update_listener`. Ozone switch checks `entry.options` to
  decide whether to enable itself. Mode command sent when option changes.

### Priority 3: Automatic clock sync
- Currently manual via a button (disabled by default).
- Fold into the options flow (Priority 2) as a boolean option.
- When enabled, the coordinator compares `spa_datetime` to HA time after each
  broadcast parse and sends a DateTime command if drift exceeds 30s (with a
  1-hour cooldown between syncs).
- Keep the manual button as a fallback (disabled by default).

### Priority 4: Polish & release
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session outcomes (latest — 2026-05-27):**
  - **All commands dynamic** — `_build_button_command()` + CRC, no replay frames.
  - **Dead code removed** — `get_temp_command()`, `get_pump_command()`,
    `get_pump_cycle_command()` aliases deleted. climate.py calls
    `build_temp_command()` directly.
  - **Terminology standardized** — hardware term "Ozone" (from manual's
    "Ozonauslass") used everywhere: UI labels, data keys (`ozone_active`),
    constants (`HEATER_OZONE`), status enum (`"ozone"`), translations.
  - **Ozone ≠ filtration** — confirmed from manual and community: ozone/UV is
    a separate device on the "Ozone Connection" port. It forces the filter pump
    on when active, but is distinct from timed filtration (command type 0xA4).
  - **Options flow planned** — ozone mode (Auto/Manual) and auto-clock-sync
    will be exposed as integration options. Design in Priority 2 above.

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests now run with pytest**:
  - Lightweight mode (no HA runtime):
    - `source .venv/bin/activate && pytest -q`
    - Current result: `76 passed, 2 skipped`.
  - HA runtime mode:
    - `source .venv-ha/bin/activate && pytest -q`
    - `.venv-ha` has `python3.12` + `homeassistant` + `pytest-homeassistant-custom-component`.
- **Protocol docs**: `docs/protocol.md` — full protocol reference.
  Captured command frames preserved in protocol.md §5 for reference.
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
