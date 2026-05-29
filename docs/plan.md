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
> **Status:** Resilient UI refactor implemented. All write tests pass.
> Persistent TCP connection, optimistic state, grace-mode availability.

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
├── __init__.py          # entry setup, coordinator lifecycle, strict unload
├── const.py             # domain, config keys, timing constants, PLATFORMS
├── manifest.json        # HACS-compatible, v0.1.0
├── config_flow.py       # IP + port, TCP connection test
├── protocol.py          # framing, unescape, CRC-32, build_frame
├── coordinator.py       # persistent TCP connection + background reader loop
├── entity.py            # device_info + JoyonwayCoordinatorEntity base class
├── sensor.py            # adapter-driven (water temp, heater/pump state, diagnostics)
├── binary_sensor.py     # bridge connectivity only
├── switch.py            # light, heater, blower, ozone, schedule slot enables (optimistic)
├── fan.py               # jets (off/low/high via preset_modes, optimistic)
├── climate.py           # thermostat with debounced slider
├── time.py              # schedule time slot start/end (8 entities, read+write, optimistic)
├── button.py            # sync spa clock to HA time (in-flight lock)
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
| **Heater** | switch | `heater` | On/off; optimistic state + target-state command |
| **Ozone** | switch | `ozone` | Manual on/off (only visible when mode=Manual); optimistic |
| **Light** | switch | `light` | On/off via toggle (toggle-lock guard, optimistic) |
| **Blower** | switch | `blower` | On/off; optimistic state + target-state command |
| **Heat slot 1 / 2** | switch | `heat_slot{n}_enabled` | Enable/disable heat schedule slots; optimistic |
| **Filter slot 1 / 2** | switch | `filter_slot{n}_enabled` | Enable/disable filter schedule slots; optimistic |
| **Jets** (Düsen) | fan | `jets` | Off/low/high via preset_modes; optimistic state |
| **Heat slot 1/2 start/end** | time | `heat_slot{n}_{start\|end}` | Read+write heat schedule times (HH:MM); optimistic |
| **Filter slot 1/2 start/end** | time | `filter_slot{n}_{start\|end}` | Read+write filter schedule times (HH:MM); optimistic |
| Sync clock | button | `sync_clock` | Sends current HA time to spa controller (disabled by default) |
| RS485 bridge | binary_sensor | `bridge_connectivity` | TCP connectivity (disabled by default) |
| Spa clock | sensor | `spa_datetime` | Diagnostic timestamp (disabled by default) |

### Key design decisions

> Protocol byte-level details (command payloads, CRC, byte maps) are in
> [`docs/protocol.md`](protocol.md). This section covers implementation choices only.

- **Persistent TCP connection** — single shared socket for reads and writes.
  Background reader loop continuously parses broadcast frames (~1–2s updates).
  Commands sent on the same socket under a write lock.
- **Reconnect with exponential backoff** — 1s → 2s → 4s → … → 30s max.
  `_connect_lock` prevents concurrent connection attempts.
- **Grace-mode availability** — entities stay available for 10s after disconnect
  to avoid UI flicker on brief interruptions. `JoyonwayCoordinatorEntity` base
  class propagates this consistently across all platforms.
- **Optimistic state** — all writable entities set pending state immediately on
  command send. Cleared when the next broadcast confirms (or after 10s timeout).
  If broadcast shows a different state, entity "snaps back" — clear visual
  feedback that the command didn't take effect.
- **Light toggle-lock** — second click ignored while toggle is in-flight
  (prevents double-toggle reverting the state).
- **Target-state switches** — heater, blower, ozone, schedule all use
  `_SpaTargetStateSwitch` base with serialized commands per entity.
- **Fan optimistic** — pending state as string (`"off"`, `"low"`, `"high"`).
  No retry loop; snap-back on mismatch from next broadcast.
- **Stale-RX health check** — fallback 60s poll detects if connection is alive
  but no data received for 15s, forces reconnect.
- **Strict unload** — `async_shutdown()` called only after platform unload
  succeeds. Cancels reader task, reconnect task, sets `_stopped=True`.
- **`entry.runtime_data`** — coordinator stored on entry for lifecycle access.
- **All commands built dynamically** — no replay-only frames.
- **Temperature setpoint**: `btn_action=0x98` confirmed working via live test.
- **Pump commands** — target-state based. Controller accepts any target directly.
- **Ozone control** — mode set via options flow, synced from broadcast byte 13.
- **Climate debounce**: 1.5s coalescing for slider drags.
- **Coordinator write pacing**: global 1.0s command cooldown.
- **Temperatures as integers** — spa only shows whole °C.
- **Schedule** — `time` entities for pickers, `switch` entities for enables.
  Schedule sends REFUSE if any data key is missing (prevents overwrite with zeros).
- **Clock write** — uses `set_date=True` (prefix=0x05) by default.
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
| 18. Safety fixes | ✅ Done | No auto writes, schedule guard, pump simplification |
| 19. Resilient UI refactor | ✅ Done | Persistent connection, optimistic state, grace availability |
| 20. Polish & release | **Next** | Live ozone test, version bump, HACS release |

## 5. Next Steps

### Priority 1: Remaining live verification
1. **Test ozone** — still untested live (mode byte 13 detection already confirmed)
2. **Verify auto clock sync** — check logs for drift-triggered sync path
3. **Live test resilient UI** — verify persistent connection, reconnect, optimistic snap-back

### Priority 2: Polish & release
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session 9 outcomes (2026-05-29):**
  - **Resilient UI fully implemented** (plan was in `docs/resilient_ui_plan.md`,
    now deleted — all content implemented and tested):
    - `coordinator.py` rewritten: persistent TCP connection, background
      `_reader_loop()`, `async_setup()`/`async_shutdown()` lifecycle,
      `_try_parse_buffer()` returns `(data, consumed)` tuple, grace-mode
      `available` property, stale-RX health check, exponential backoff reconnect.
    - `entity.py`: added `JoyonwayCoordinatorEntity` base class with
      availability from coordinator grace logic.
    - `__init__.py`: calls `async_setup()`, stores `entry.runtime_data`,
      strict shutdown on unload (only after platform unload succeeds).
    - `switch.py`: `SpaLightSwitch` has toggle-lock guard + optimistic state.
      New `_SpaTargetStateSwitch` base for heater/blower/ozone/schedule.
      All have `_pending_state`, `_handle_coordinator_update` clearing,
      and `OPTIMISTIC_TIMEOUT_SECONDS` auto-expire.
    - `fan.py`: optimistic `_pending_state` (str), removed 3-retry loop.
    - `time.py`: optimistic `_pending_state` (tuple).
    - `button.py`: in-flight `_cmd_lock`.
    - `climate.py`: removed `async_request_refresh()`.
    - `sensor.py`, `binary_sensor.py`: switched to `JoyonwayCoordinatorEntity`.
  - **All `async_request_refresh()` removed** — reader loop pushes updates.
  - **All `asyncio.sleep(1.0)` after light toggle removed**.
  - **`COMMAND_COOLDOWN` moved to `const.py`** (was local in coordinator).
  - **`SCAN_INTERVAL` changed from 30 to 60** (fallback health-check only).
  - **Tests updated**: `DummyCoordinator` stubs updated for persistent model.
    New tests: `test_light_double_click_blocked`, `test_heater_optimistic_state`,
    `test_fan_optimistic_preset_mode`. Fixed stale CMD_ constant imports
    (commands are now built dynamically via adapter).
    New file `test_coordinator_resilient.py` with advanced coordinator tests:
    shutdown races, reconnect guards, availability grace, stale-RX, command
    send, buffer parsing, optimistic timeout auto-clear/cancel/removal.
    HA venv: 109 passed. Non-HA venv: 76 passed, 3 skipped.
  - **README updated**: added persistent connection + optimistic UI to features.

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests**: `source .venv/bin/activate && pytest -q` → `76 passed, 3 skipped`.
  With HA: `source .venv-ha/bin/activate && pytest -q` → `109 passed`.
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
