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
- **Strict connectivity diagnostic** — `bridge_connectivity` uses raw TCP state
  (`coordinator.is_connected`), not grace-mode availability.
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
- **Jets semantics (intentional)** — `jets` represents user/manual jets state
  only (off/low/high from the jets byte). Circulation/heating pump activity must
  NOT be surfaced as jets-on. This matches PB554 panel semantics and avoids
  confusing control behavior (manual jets should remain independently controllable).
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

### Priority 1: Remaining live verification
1. **Test ozone** — still untested live (mode byte 13 detection already confirmed)
2. **Verify auto clock sync** — check logs for drift-triggered sync path
3. **Live test resilient UI** — verify persistent connection, reconnect, optimistic snap-back

### Priority 2: Polish & release
- Version bump, final release checklist review, HACS release

### Priority 3: Safety hardening (next implementation)
- Add schedule freshness gating before any schedule write (`switch` and `time`):
  require a recent schedule snapshot (timestamp-based), optionally wait briefly
  for next broadcast if stale, then refuse write with a clear error if still stale.

### Priority 4: Diagnostics enrichment (next implementation)
- Capture and expose controller diagnostic metadata from frames, starting with
  firmware/version fields (visible on PB554 panel, expected in RS485 payload).

### Priority 5: Hardware capability options (next implementation)
- Add a "Hardware" section in options/config where users can declare whether a
  blower is physically present. If blower is not present, do not create/show the
  blower switch entity at all.

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
- **Session 10 (2026-05-29):** Merged `.venv-ha` into `.venv` (Python 3.12
  with full HA test stack). Removed old `ha-test` extra from `pyproject.toml`;
  `[test]` now includes `pytest-homeassistant-custom-component`. Updated
  README testing section to single-venv instructions.
- **Session 11 (2026-05-29):** Completed code-quality/best-practice follow-up.
  Fixed ozone switch unique ID (`_ozone_switch`), added strict TCP connectivity
  property for diagnostic sensor, rate-limited auto clock sync on failed sends,
  cleaned fan docs/type hints, removed stale TODO in `__init__.py`, and applied
  small cleanups (unused fields/imports/constants, helper dedup). Tests: `109 passed`.
- **Session 12 (2026-05-29):** Follow-up UX/scope decisions. Blower switch set
  disabled-by-default for cleaner default layout; ozone switch hidden entirely in
  Auto mode (still available in Manual) while preserving switch row order when
  shown. Community feedback TODO trimmed to currently relevant items. Added next
  TODOs for schedule freshness gating and firmware/version diagnostics capture.
- **Session 13 (2026-05-29):** Terminology/doc polish. German blower label set to
  `Luftsprudler`; French blower label set to `Souffleur d'air`. Manual review in
  `.local/home-deluxe-white-marble.md` suggests blower may be absent on this spa
  build, so docs now describe blower as optional hardware.
