# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller,
> with a model adapter interface ready for future multi-model expansion.
>
> **Repo:** `alexbde/ha-joyonway` (migrated from `ha-joyonway-p25b85`)
> **Upstream:** christopheknap keeps `ha-joyonway-p23b32` P23B32-only.
> His code remains at https://github.com/KnapTheBuilder/ha-joyonway-p23b32.
>
> **Integration domain:** `joyonway`
> **Hardware:** P25B85 + PB554 + Elfin EW11
> **Status:** Resilient UI refactor implemented with intent-queue follow-up fixes merged.
> Persistent TCP connection, optimistic state, grace-mode availability, explicit
> schedule-write failure path (no silent no-op on missing schedule data).

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
custom_components/joyonway/
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
- **Schedule freshness gating** — removed (previously `async_ensure_fresh_data()`).
  Now handled naturally by the intent queue: build_fn reads current coordinator
  data at drain time (after coalesce window), which is always as fresh as the
  latest broadcast. If required schedule data is missing, writes fail explicitly
  (`HomeAssistantError` on submit path, `IntentBuildError` on queue drain).
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
- **Status cross-reference** — byte 14 value `0x50` = "standby" (heater armed,
  0W). Byte 14 value `0x51` = "circulation" (pre-heat pump running, circle icon).
  Post-heat circulation detected via byte 17 bit 7 (`0x80`): when byte 14 =
  `0x40` (off) but heating cycle flag is set, status = "circulation". Both
  pre-heat and post-heat circulation map to the same "circulation" status and
  `HVACAction.PREHEATING` in the climate entity.
- **Ozone control** — mode set via options flow, synced from broadcast byte 13.
- **Ozone visibility** — ozone switch is only created in Manual mode; hidden in
  Auto mode to keep UI cleaner and avoid disabled-but-visible controls.
- **Blower visibility** — blower is optional hardware; switch stays disabled by
  default to avoid clutter on builds without physical blower support.
  Planned migration: move from disabled-by-default to capability-driven creation
  via a Hardware options section (user declares blower present/absent).
- **Climate debounce**: 1.5s coalescing for slider drags.
- **Coordinator write pacing**: global 1.0s command cooldown.
- **Intent queue** — all entity write commands go through `IntentQueue` on the
  coordinator. Same-group intents coalesce within a 300ms window. Key behaviors:
  - **Coalescing**: rapid clicks on related entities (e.g., heat slot 1 + 2)
    merge into a single command.
  - **Auto-cancel**: accidental toggle (ON→OFF within 300ms) produces a no-op
    at drain time — no command sent, pending state clears on next broadcast.
  - **Sequential drain**: all command types queue up and execute one at a time,
    preventing bus contention regardless of command type.
  - **Retry**: one retry on TCP send failure.
  - **Groups**: `heat_schedule_state`, `filter_schedule_state`,
    `heat_schedule_time`, `filter_schedule_time`, `heater`, `blower`,
    `ozone`, `light`, `jets`, `setpoint`, `clock_sync`.
  - Entity sets optimistic state immediately on submit (instant UI).
  - On failure after retry, entity's `on_failure` callback clears pending state.
- **Temperatures as integers** — spa only shows whole °C.
- **Schedule** — `time` entities for pickers, `switch` entities for enables.
  Schedule sends REFUSE if any data key is missing (prevents overwrite with zeros).
- **Schedule command intent split (confirmed)** — schedule writes use two flag
  modes: `write_mode="state"` for enable toggles (`0xAA/0x62/0x9A/0x52`) and
  `write_mode="time"` for time edits (`0xAA/0x6A/0x9A/0x5A`). This matches
  PB554 panel captures and fixes slot 2 time writes when slot 2 is disabled.
- **Clock write** — uses `set_date=True` (prefix=0x05) by default.
- **Auto clock sync** — disabled by default. When enabled, syncs if drift > 30s
  with 1-hour cooldown. Cooldown now applies to both successful syncs and failed
  attempts (prevents repeated retries/log spam during failures).
- **No auto commands on startup** — all writes are user-initiated only.
- **All commands routed through intent queue** — no entity or internal function
  calls `async_send_command` directly. Clock sync, ozone mode, and all entity
  writes go through the queue for consistent serialization.
- **Non-silent snap-back** — if a pending optimistic state times out (controller
  didn't confirm within 10s), a WARNING-level log is emitted identifying the
  entity and the failed action. This ensures no state reversion is ever silent.
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

### Priority 1: Schedule slot 2 write bug — ✅ FIXED
**Bug (resolved):** Changing time on disabled slot 2 via HA snapped back after
10s because the controller ignores slot 2 time values when the normal
"disabled" flags byte is used (`0x52` / `0x62`).

**Root cause:** Asymmetric controller behavior — slot 1 times always apply,
but slot 2 times are only accepted when slot 2 is enabled in the flags byte.

**Final fix (confirmed):** schedule commands now use two intent modes:
- **State mode** (slot enable/disable): `0xAA`, `0x62`, `0x9A`, `0x52`
- **Time mode** (time edits): `0xAA`, `0x6A`, `0x9A`, `0x5A`

`0x6A` for s1-on/s2-off slot 2 time edits was captured live from PB554 for
both heat and filter schedules.

**Capture evidence:**
- `tools/captures_schedule_slot2/capture_slot2_20260531_091843.jsonl` (slot2 disabled case, `0x58`)
- `tools/captures_schedule_s1_on_s2_off/session_20260531_161502.jsonl` (s1-on/s2-off slot2 edit, `0x6A`)
- `tools/captures_schedule_test/slot_test_20260531_164513.jsonl` (first reliable HA-UI matrix, 50/0 pass)

### Priority 2: Remaining live verification
1. **Test ozone** — still untested live (mode byte 13 detection already confirmed)
2. **Verify auto clock sync** — check logs for drift-triggered sync path
3. **Live test resilient UI** — verify persistent connection, reconnect, optimistic snap-back
   - ✅ **Intent queue implemented** (session 22): all entity commands go through
     `IntentQueue` which coalesces same-group intents, serializes bus writes, and
     auto-cancels reverted clicks. The "rapid button clicks revert each other"
     problem is solved. Remaining: live verification of reconnect + snap-back.
4. **Test schedule writes** — ✅ completed. Reliable HA-UI live matrix now
   covers all UI-reachable schedule combinations (state toggles + single-field
   time edits across all enable combos), with retries and convergence waits.

### Priority 3: Diagnostics enrichment (next implementation)
- Capture and expose controller diagnostic metadata from frames, starting with
  firmware/version fields (visible on PB554 panel, expected in RS485 payload).

### Priority 4: Hardware capability options (next implementation)
- Add a "Hardware" section in options/config where users can declare whether a
  blower is physically present. If blower is not present, do not create/show the
  blower switch entity at all.

### Priority 5: Repository rename + fresh repo
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
  2. Rename `custom_components/joyonway/` → `custom_components/joyonway/`.
  3. Update all internal references: domain in `const.py`, `manifest.json`,
     `hacs.json`, `config_flow.py`, `__init__.py`, translations, tests.
  4. Squash history into clean meaningful commits (or single initial commit).
  5. Push to new repo.
  6. Update README attribution: "Originally developed in `ha-joyonway-p25b85`,
     inspired by christopheknap's `ha-joyonway-p23b32`."
  7. Archive old `alexbde/ha-joyonway-p25b85` repo (or delete after transition).
  8. Update HACS repository URL if already registered.

### Priority 6: Polish & release
- Version bump, final release checklist review, HACS release

### Nice to have (post-release UX)
- Lovelace dashboard package/example for spa controls using community cards
  (compact grouped layout for schedules/options without changing entity model).

## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests**: `source .venv/bin/activate && pytest -q` → `120 passed`.
  Single venv (Python 3.12 + HA test deps via `pip install -e ".[test]"`).
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
- **Community feedback source**: https://community.home-assistant.io/t/joyonway-spa-control/582344/
- **Historical safety context**: early field reports indicated possible config
  corruption/factory-reset recovery on some setups; current mitigations are
  no auto-writes on startup, strict schedule-data guards, and resilient connection
  behavior. Keep these protections in place.
- **Session 14 (2026-05-30):** Fixed optimistic state snap-back bug — pending
  state only cleared when broadcast confirms new value. Implemented schedule
  freshness gating.
- **Session 15 (2026-05-31):** Remapped byte 14 `0x50` from "circulation" →
  "standby" (heater armed, 0W idle). Byte 12 confirmed as manual jets only.
- **Session 16 (2026-05-31):** Investigated schedule slot 2 write bug. Controller
  ignores slot 2 time values when slot 2 is disabled in the flags byte. Created
  `tools/capture_schedule_slot2.py` to capture panel behavior.
- **Session 17 (2026-05-31):** Heating cycle fully captured and analyzed.
  Confirmed `0x51` = pre-heat circulation (circle icon). Discovered byte 17
  bit 7 (`0x80`) = "heating cycle active" flag — set during entire cycle
  (pre-heat → heating → post-heat). Post-heat circulation = byte 14 `0x40` +
  byte 17 `0x80` (circle icon, ~2 min after heating stops). Byte 28 bit 5
  (`0x20`) mirrors same state. Updated `parse_status()` to detect post-heat
  circulation. Added `MASK_HEATING_CYCLE = 0x80`. Rewrote capture script to
  save all frames as JSONL with byte-change tracking. Created
  `analyze_heating_frames.py` for post-hoc analysis. Tools in
  `tools/captures_heating/`. Tests: `111 passed`.
- **Session 18 (2026-05-31):** Fixed schedule slot 2 write bug. Panel capture
  revealed the PB554 uses flags byte `0x58` when writing disabled slot 2
  times (panel flow: enable → edit → disable → save). Implemented
  `force_slot2_write` parameter in `build_schedule_command` and automatic
  detection in `time.py`. Added `SCHED_FLAGS_FORCE_SLOT2_TABLE` with `0x58`
  (confirmed) and `0x68` (derived). Capture data in
  `tools/captures_schedule_slot2/`.
- **Session 19 (2026-05-31):** Comprehensive schedule slot write verification.
  Created `tools/test_schedule_slots.py` (automated write tests with full raw
  binary capture) and `tools/capture_schedule_changes.py` (guided 4-step panel
  capture for slot 1/2 × heat/filter while disabled). Also created
  `tools/capture_schedule_both_slots.py` for both-slots-at-once captures.
  Live captures confirmed three distinct flags bytes:
  - `0x52` = slot 1 edited while disabled (slot 1 always accepted)
  - `0x58` = slot 2 edited while disabled (force-writes slot 2)
  - `0x5A` = both slots edited while disabled (force-writes both)
  Simplified implementation: always use `0x5A` for both-disabled case so
  slot 1 and slot 2 behave identically. Removed asymmetric `force_slot2_write`
  logic — replaced `SCHED_FLAGS_FORCE_SLOT2_TABLE` with
  `SCHED_FLAGS_FORCE_WRITE_TABLE`. Updated `protocol.md` with full findings.
  **Verified live:** 8/8 tests passed — `0x5A` works correctly when changing
  only slot 1, only slot 2, or both slots simultaneously (heat + filter).
  All user panel confirmations positive. Capture data in
  `tools/captures_schedule_changes/`, `tools/captures_schedule_both/`, and
  `tools/captures_schedule_test/`. Tests: `113 passed`.
- **Session 20 (2026-05-31):** Resolved s1-on/s2-off slot2 time-edit flags.
  Created focused capture script `tools/capture_schedule_s1_on_s2_off.py` and
  verified panel sends `0x6A` (not `0x68`) for both heat and filter when slot
  1 is enabled, slot 2 disabled, and slot 2 time is edited. Updated
  `docs/protocol.md` and implementation to split schedule intent:
  - state mode flags: `0xAA/0x62/0x9A/0x52`
  - time mode flags: `0xAA/0x6A/0x9A/0x5A`
- **Session 21 (2026-05-31):** Reworked the schedule live runner into a
  reliable HA-UI-realistic test (no manual confirmation). Added retries,
  convergence waiting, and robust restore. Coverage: all state combos + all
  single-field time edits across all enable combos for heat and filter.
  Live result: **50 passed, 0 failed**.
  Runner path: `tests/live/livetest_schedule_ui_matrix.py`.
  New artifact directory: `tests/live/artifacts_schedule_matrix/`.
- **Session 22 (2026-05-31):** Implemented `IntentQueue` in coordinator to solve
  rapid-action reversion. All entity write commands (switches, fan, climate,
  time, button) now submit intents to the queue instead of calling
  `async_send_command` directly. Key features: 300ms coalesce window,
  same-group merging (schedule slot 1+2 → one command), auto-cancel on
  revert (ON→OFF = no-op), sequential drain (no bus contention across any
  command types), retry on TCP failure. Removed per-entity `_cmd_lock` for
  target-state switches (serialization now handled by queue). Light retains
  its toggle-lock (toggle semantics require it). Follow-up polish: removed
  dead `async_ensure_fresh_data()` + constants (superseded by intent queue),
  fixed climate optimistic state (pending temp stays until broadcast confirms,
  with 10s timeout), routed ALL command paths through the queue (clock sync
  in coordinator, ozone mode in `__init__.py`), added WARNING-level logs on
  all pending-state timeouts (non-silent snap-back). Tests: `114 passed`.
- **Session 23 (2026-05-31):** Intent-queue follow-up bugfix/polish pass.
  Fixed options-flow race where ozone mode command could be dropped on reload:
  `_async_options_updated()` now submits then `await intent_queue.flush()`
  before config-entry reload. Added explicit intent-build failure path via
  `IntentBuildError` handling in `IntentQueue` (build failures now trigger
  `on_failure` callbacks instead of silent no-op behavior). Schedule writes now
  fail explicitly on missing required data (`HomeAssistantError` in service
  path; `IntentBuildError` at queue-drain path). Added/updated regression tests:
  - `tests/test_coordinator_resilient.py` (flush + build failure callbacks)
  - `tests/test_entities_runtime.py` (schedule missing-data errors)
  - `tests/test_init_runtime.py` (options update order: submit → flush → reload)
  Also cleaned code style in `IntentQueue` by deduplicating failure-callback
  dispatch into a helper and corrected ozone log wording to avoid implying
  guaranteed send success. Tests: `120 passed`.
