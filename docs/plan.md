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
> **Status:** All 8 write tests pass (session 5). Date-write solved (session 6).
> Critical community feedback received: integration corrupted one user's spa
> (factory reset required). Safety fixes needed before release.

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

> Protocol byte-level details (command payloads, CRC, byte maps) are in
> [`docs/protocol.md`](protocol.md). This section covers implementation choices only.

- **All commands built dynamically** — no replay-only frames.
  `_build_button_command()` is the universal builder for type-0xA1 commands.
- **Temperature setpoint**: `btn_action=0x98` confirmed working via live test.
  `0x80` was tested and does NOT work.
- **Ozone two-step control** — ON: send mode→Manual, delay 1.5s, send manual ON.
  OFF: send manual OFF, delay 1.5s, send mode→Auto.
- **Ozone switch availability** — controlled by options flow. When ozone mode is
  "auto" (default), the switch is unavailable (grayed out). When "manual", the
  switch becomes available for RS485 control.
- **Fan = "Jets" / "Düsen"** — matches spa manual terminology.
- **Fan entity retry logic** — pump commands retry up to 3× with state check.
  RS485 bus collisions can cause commands to be lost. Retry handles this.
- **Light toggle** — `_send_toggle()` has 1.0s delay before refresh to avoid
  reading a stale broadcast (race condition fix).
- **Heater byte blower-flag fix** — `parse_status()` strips blower bit via
  `heater_base = heater_byte & ~MASK_HEATER_BLOWER` before status lookup.
- **Climate debounce**: 1.5s coalescing for slider drags.
- **Coordinator write pacing**: global 1.0s command cooldown.
- **Temperatures as integers** — spa only shows whole °C.
- **Schedule** — `time` entities for pickers, `switch` entities for enables.
- **Clock write** — uses `set_date=True` (prefix=0x05) by default, writing both
  date and time. Pass `set_date=False` for time-only sync.
- **Options flow** — two options: ozone mode (Auto/Manual) and auto clock sync
  (bool, default ON). Stored in `entry.options`, reload on change.
- **Auto clock sync** — coordinator compares `spa_datetime` to HA time after
  each broadcast parse. Syncs if drift > 30s, with 1-hour cooldown.

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1–6 | ✅ Done | Capture, integration, byte map, writes, temp control |
| 7. Live test writes | **Mostly done** | See live test results below |
| 8–16 | ✅ Done | Schedule, CRC, DateTime, ozone, dynamic commands |
| 17. Options flow | ✅ Done | Ozone mode + auto clock sync |
| 18. Polish & release | Planned | After remaining items verified |

### Live test results (session 5, 2026-05-28 evening — ALL PASS)

| Test | Result | Notes |
|------|--------|-------|
| Light | ✅ PASS | Toggle ON/OFF both confirmed |
| Heater | ✅ PASS | ON (circulation) and OFF confirmed |
| Blower | ✅ PASS | ON (`0x0C`) and OFF (`0x00`) both confirmed |
| Jets | ✅ PASS | off→low ✅, low→high ✅, high→off ✅ (needed 1 retry — RS485 collision) |
| Temperature | ✅ PASS | `btn_action=0x98` works, 37→38→37 round-trip |
| Heat schedule | ✅ PASS | All fields + enable/disable confirmed |
| Filter schedule | ✅ PASS | All fields + enable/disable + restore confirmed |
| Clock | ✅ PASS | H:M:S confirmed (session 5). Date+time confirmed (prefix=0x05, session 6) |

**Date write — SOLVED:** The prefix byte controls behaviour. `0x50` = time-only
(what we were sending previously), `0x05` = date+time (what the panel sends).
Captured from PB554 panel and verified. Integration updated to use `0x05`.
See [`docs/protocol.md` §4.2](protocol.md#42-datetime-set-type-0xa2) for details.

## 5. Next Steps

### Priority 0: Fix critical community feedback (SAFETY)
Community users KDy and old-man tested the integration on their spas.
**KDy's spa required a factory reset** after the integration corrupted his
configuration. Full analysis and fix list: [`docs/community_feedback_todo.md`](community_feedback_todo.md)

Key fixes needed:
1. **Never auto-send commands on startup/reload** — audit all write paths
2. **Schedule overwrite guard** — refuse to send with missing/default data
3. **Jets OFF** — doesn't work for KDy (model or state-tracking issue)
4. **Ozone UX** — users don't understand why it's inactive

### Priority 1: Remaining live tests
1. **Test ozone** — untested live (auto-cycle was observed passively during session 5)
2. **Verify auto clock sync** — check logs for "Spa clock drift" messages

### Priority 2: Polish & release
- UI feedback delay: after sending a command, there's a ~2s gap before the
  broadcast updates. Consider optimistic state updates, a spinner, or similar
  UX to bridge the gap so the UI doesn't feel laggy.
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session outcomes (latest — 2026-05-28, session 6):**
  - **Date write SOLVED** — prefix byte `0x05` writes date+time, `0x50` writes
    time only. Captured 3 panel commands (2 date changes + 1 time-only change)
    confirming this. `build_datetime_command()` updated with `set_date` kwarg.
  - **Protocol/plan separation** — moved all byte-level protocol data out of
    plan.md into protocol.md. Plan now links to protocol for details.
  - **protocol.md updated** — DateTime §4.2 prefix byte table, button table
    updated with live-confirmed values (blower `0x0C`, temp `0x98`).

- **Previous sessions:**
  - Session 5 (2026-05-28 evening): All 8 write tests pass live. Community
    feedback received (KDy factory reset). `docs/community_feedback_todo.md` created.
  - Session 4 (2026-05-28 afternoon): TCP buffer fix, temp cmd fixed (0x98),
    blower OFF → 0x00, jets retry logic.
  - Session 3 (2026-05-28): Heater byte blower-flag fix, light toggle race fix.
  - Session 2 (2026-05-27): Options flow, auto clock sync, ozone, translations.
  - Session 1: Initial integration, byte map, CRC cracking, all entities.

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests**: `source .venv/bin/activate && pytest -q` → `76 passed, 2 skipped`.
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
