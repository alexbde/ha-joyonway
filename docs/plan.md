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
> **Status:** Resilient UI refactor implemented and post-refactor code review/polish completed.
> Persistent TCP connection, optimistic state, grace-mode availability for entities.

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
- **Blower:** optional controller load; not confirmed on all spa builds.
  Local White Marble manual does not clearly document a dedicated air blower.

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
| **Status** | sensor | `status` | Enum: off / standby / circulation / heating / ozone / unknown; dynamic icon per state |
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
- **Strict connectivity diagnostic** — `bridge_connectivity` uses raw TCP state
  (`coordinator.is_connected`), not grace-mode availability.
- **Optimistic state** — all writable entities set pending state immediately on
  command send. Cleared only when the next broadcast **confirms** the new value
  (or after 10s timeout). Confirmation logic is entity-specific via
  `_broadcast_confirms_pending()` template method in switches, direct comparison
  in fan/time. If broadcast still shows old state, pending persists — snap-back
  only occurs on timeout expiry, giving clear visual feedback.
- **Schedule freshness gating** — before any schedule write (time or enable),
  `coordinator.async_ensure_fresh_data()` verifies last broadcast ≤5s old.
  If stale, waits up to 3s for a fresh one, then refuses with error. Prevents
  writing schedule commands based on outdated data.
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
- **Jets semantics (intentional)** — `jets` represents user/manual jets state
  only (off/low/high from the jets byte). Circulation/heating pump activity must
  NOT be surfaced as jets-on. This matches PB554 panel semantics and avoids
  confusing control behavior (manual jets should remain independently controllable).
- **Status cross-reference** — byte 14 value `0x50` was originally mapped as
  "circulation" (from KDy). Capture analysis confirms `0x50` appears with
  pump=0x00 for entire idle periods (0W energy), so it actually means "heater
  enabled/armed" (standby). Currently mapped to "standby". The actual
  circulation phase (pre/post-heat pump running at ~300W) may use a different
  byte 14 value not yet captured. "circulation" is kept as a valid enum state
  pending a full heating cycle capture to identify the correct byte value.
- **Ozone control** — mode set via options flow, synced from broadcast byte 13.
- **Ozone visibility** — ozone switch is only created in Manual mode; hidden in
  Auto mode to keep UI cleaner and avoid disabled-but-visible controls.
- **Blower visibility** — blower is optional hardware; switch stays disabled by
  default to avoid clutter on builds without physical blower support.
  Planned migration: move from disabled-by-default to capability-driven creation
  via a Hardware options section (user declares blower present/absent).
- **Climate debounce**: 1.5s coalescing for slider drags.
- **Coordinator write pacing**: global 1.0s command cooldown.
- **Temperatures as integers** — spa only shows whole °C.
- **Schedule** — `time` entities for pickers, `switch` entities for enables.
  Schedule sends REFUSE if any data key is missing (prevents overwrite with zeros).
- **Clock write** — uses `set_date=True` (prefix=0x05) by default.
- **Auto clock sync** — disabled by default. When enabled, syncs if drift > 30s
  with 1-hour cooldown. Cooldown now applies to both successful syncs and failed
  attempts (prevents repeated retries/log spam during failures).
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
| 20. Polish & release | **In progress** | Code polish + consistency fixes done; live ozone test, version bump, HACS release remain |

## 5. Next Steps

### Priority 1: Heating cycle capture & status verification
1. **Capture full heating cycle** — run `tools/capture_heating_cycle.py` while
   enabling/disabling the heater to see all byte 14 transitions through the
   natural cycle: standby → circulation? → heating → circulation? → standby.
2. **Identify circulation byte** — determine which byte 14 value (if any)
   corresponds to the actual circulation phase (~300W pump pre/post-heat).
   Possibly `0x51`, or a new value not yet seen.
3. **Adjust status mapping** — based on results, either assign "circulation" to
   the correct byte value or remove it if circulation has no distinct state.

### Priority 2: Remaining live verification
1. **Test ozone** — still untested live (mode byte 13 detection already confirmed)
2. **Verify auto clock sync** — check logs for drift-triggered sync path
3. **Live test resilient UI** — verify persistent connection, reconnect, optimistic snap-back

### Priority 2: Diagnostics enrichment (next implementation)
- Capture and expose controller diagnostic metadata from frames, starting with
  firmware/version fields (visible on PB554 panel, expected in RS485 payload).

### Priority 3: Hardware capability options (next implementation)
- Add a "Hardware" section in options/config where users can declare whether a
  blower is physically present. If blower is not present, do not create/show the
  blower switch entity at all.

### Priority 4: Repository rename + fresh repo
- ✅ **Decision: fresh repo.** Divergence analysis (session 15) confirmed zero
  shared code with upstream (9 vs 113 commits, different domain/architecture).
  Merge/rebase is meaningless. Instead of detaching the fork, create a clean
  new repo with a better name and squashed history.
- **Target naming:**
  - Repo: `alexbde/ha-joyonway`
  - Integration domain: `joyonway`
  - Directory: `custom_components/joyonway/`
- **Execution checklist:**
  1. Create fresh GitHub repo `alexbde/ha-joyonway` (no fork relationship).
  2. Rename `custom_components/joyonway_p25b85/` → `custom_components/joyonway/`.
  3. Update all internal references: domain in `const.py`, `manifest.json`,
     `hacs.json`, `config_flow.py`, `__init__.py`, translations, tests.
  4. Squash history into clean meaningful commits (or single initial commit).
  5. Push to new repo.
  6. Update README attribution: "Originally developed in `ha-joyonway-p25b85`,
     inspired by christopheknap's `ha-joyonway-p23b32`."
  7. Archive old `alexbde/ha-joyonway-p25b85` repo (or delete after transition).
  8. Update HACS repository URL if already registered.

### Priority 5: Polish & release
- Version bump, final release checklist review, HACS release

### Nice to have (post-release UX)
- Lovelace dashboard package/example for spa controls using community cards
  (compact grouped layout for schedules/options without changing entity model).

## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests**: `source .venv/bin/activate && pytest -q` → `109 passed`.
  Single venv (Python 3.12 + HA test deps via `pip install -e ".[test]"`).
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
- **Community feedback source**: https://community.home-assistant.io/t/joyonway-spa-control/582344/
- **Historical safety context**: early field reports indicated possible config
  corruption/factory-reset recovery on some setups; current mitigations are
  no auto-writes on startup, strict schedule-data guards, and resilient connection
  behavior. Keep these protections in place.
- **Session 10 (2026-05-29):** Merged `.venv-ha` into `.venv`; single Python 3.12
  venv with full HA test stack.
- **Session 11 (2026-05-29):** Code-quality follow-up (ozone unique ID, TCP
  connectivity property, clock sync rate limit, cleanups).
- **Session 12 (2026-05-29):** UX decisions (blower disabled-by-default, ozone
  hidden in Auto mode).
- **Session 13 (2026-05-29):** Terminology/doc polish (German/French blower labels,
  blower documented as optional hardware).
- **Session 14 (2026-05-30):** Fixed optimistic state snap-back bug for all
  writable entities — pending state now only cleared when broadcast confirms
  the new value (via `_broadcast_confirms_pending()` template method). Applied
  to all switches, fan, and time entities. Implemented schedule freshness gating
  (`coordinator.async_ensure_fresh_data()`): verifies last broadcast ≤5s old
  before any schedule write, waits up to 3s for fresh data, refuses if still
  stale. Tests: `109 passed`.
- **Session 15 (2026-05-31):** Status sensor fix. Byte 14 = `0x50` was mapped
  as "circulation" (from KDy) but capture analysis + energy monitoring proves
  it means "heater armed/standby" (shows for hours at 0W). Remapped `0x50` →
  "standby". Byte 12 confirmed to be manual jets only (independent of heater
  cycle). "circulation" kept as valid enum state pending full heating cycle
  capture — the actual circulation phase (~300W) may use a different byte 14
  value not yet observed. Added `capture_heating_cycle.py` tool to capture
  the full cycle. Tests: `111 passed`.
