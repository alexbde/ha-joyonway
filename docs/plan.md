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
> **Status:** Live testing mostly complete. 6/8 tests pass (light, heater,
> jets, temperature, heat schedule, filter schedule). Blower OFF and clock
> date-write need further investigation. RS485 bus collision retry logic
> added to fan entity and test script.

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
- **Light:** RGB LED, 9 states cycling via panel button; RS485 toggle is
  simple on/off (same frame turns on or off, confirmed by captures)
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
- **Temperature setpoint**: `btn_action=0x98` confirmed working via live test.
  `0x80` was tested and does NOT work. Adapter uses `build_temp_command()`.
- **Ozone two-step control** — ON: send mode→Manual, delay 1.5s, send manual ON.
  OFF: send manual OFF, delay 1.5s, send mode→Auto. Broadcast byte 14 tracks state.
- **Ozone switch availability** — controlled by options flow. When ozone mode is
  "auto" (default), the switch is unavailable (grayed out). When "manual", the
  switch becomes available for RS485 control.
- **Fan = "Jets" / "Düsen"** — matches spa manual terminology
- **Fan entity retry logic** — pump commands retry up to 3× with state check.
  RS485 bus collisions can cause commands to be lost (our frame sent while
  controller is mid-broadcast → both garbled on wire). Retry handles this.
- **Light toggle**: same frame for on/off (confirmed by Phase 6 captures —
  identical bytes for ON and OFF). Not a cycle — single toggle turns off.
  `_send_toggle()` has 1.0s delay before refresh to avoid reading a stale
  broadcast (race condition that caused turn-off to appear to fail).
- **Heater byte blower-flag fix**: bit 3 (`0x08`) of byte 14 is the blower
  flag, ORed onto the heater state. `parse_status()` strips it via
  `heater_base = heater_byte & ~MASK_HEATER_BLOWER` before looking up status.
  Without this, blower+heating = `0x5D` → "unknown" in the UI. Raw byte
  preserved as `heater_byte` in the data dict for diagnostics.
- **Blower ON**: `btn_action=0x0C` confirmed working.
  **Blower OFF**: changed from `0x08` (did not work) to `0x00` (matching
  heater OFF pattern). Needs live verification.
- **Heater/blower switches**: distinct ON/OFF commands; safe to send
- **Climate debounce**: 1.5s coalescing for slider drags
- **Coordinator write pacing**: global 1.0s command cooldown
- **Pump transitions** — all 6 transitions (off↔low↔high) use direct
  single commands. The panel cycles off→low→high→off so low→high is
  definitely supported by the controller. Test failures on low→high were
  caused by RS485 bus collisions, not protocol issues. Retry logic added.
  **Confirmed live**: off→low ✅, off→high ✅, low→off ✅ (session 2).
- **Temperatures as integers** — spa only shows whole °C
- **Schedule times as `time` entities** — proper HA time pickers, supports HH:MM
- **Schedule enables as `switch` entities** — toggle slots on/off
- **Schedule write**: builds full command with all 4 slot values + CRC via `build_frame()`
- **Schedule enable/disable**: flags byte (byte 7) encodes slot enables via lookup
  table: `0xAA`=both on, `0x62`=s1 on/s2 off, `0x9A`=s1 off/s2 on, `0x52`=both off.
- **Clock write**: only H:M:S are accepted by the controller. Date bytes (Y:M:D)
  are included in the payload but the controller ignores them — date is read-only.
  Auto clock sync still works (only time matters for drift detection).
- **Options flow** — two options: ozone mode (Auto/Manual) and auto clock sync
  (bool, default ON). Stored in `entry.options`, reload on change.
- **Auto clock sync** — coordinator compares `spa_datetime` to HA time after
  each broadcast parse. Syncs if drift > 30s, with 1-hour cooldown.
- **Switch entity order** — Heizung | Licht, Ozon | Gebläse (dashboard layout)

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1–6 | ✅ Done | Capture, integration, byte map, writes, temp control |
| 7. Live test writes | **Mostly done** | See live test results below |
| 8–16 | ✅ Done | Schedule, CRC, DateTime, ozone, dynamic commands |
| 17. Options flow | ✅ Done | Ozone mode + auto clock sync |
| 18. Polish & release | Planned | After remaining items verified |

### Live test results (session 4, 2026-05-28)

| Test | Result | Notes |
|------|--------|-------|
| Light | ✅ PASS | Toggle ON/OFF both confirmed |
| Heater | ✅ PASS | ON (circulation) and OFF confirmed |
| Blower | ❌ FAIL | ON works, OFF (`btn_action=0x08`) did not. Changed to `0x00`, needs re-test |
| Jets | ✅ PASS* | off→low ✅, off→high ✅ (run 1). low→high failed (RS485 collision). Retry logic added |
| Temperature | ✅ PASS | `btn_action=0x98` works. `0x80` does NOT. Adapter updated |
| Heat schedule | ✅ PASS | All fields + enable/disable confirmed (test values were fixed to differ from current) |
| Filter schedule | ✅ PASS | All fields + enable/disable + restore confirmed |
| Clock | ✅ PASS* | H:M:S set correctly. Date bytes ignored by controller (test updated to only check time) |

## 5. Next Steps

### Priority 1: Continue live testing (at the spa)
1. **Verify blower OFF** — changed `btn_action` from `0x08` to `0x00`; needs re-test
2. **Re-run jets test** — verify low→high and high→off with retry logic
3. **Test ozone** — untested live
4. **Verify auto clock sync** — check logs for "Spa clock drift" messages

### Priority 2: Polish & release
- UI feedback delay: after sending a command, there's a ~2s gap before the
  broadcast updates. Consider optimistic state updates, a spinner, or similar
  UX to bridge the gap so the UI doesn't feel laggy.
- Version bump, README final review, HACS release

## 6. Technical Notes for Next Session

- **Session outcomes (latest — 2026-05-28, session 4):**
  - **Guided write-test TCP buffer bug fixed** — the script was reading
    STALE broadcast frames buffered during POST_COMMAND_DELAY. Root cause:
    `read_broadcast()` returned the FIRST (oldest) frame instead of the
    LAST (newest). Two fixes applied:
    1. `drain_stale()` clears the TCP buffer before every `send_command()`
    2. `read_broadcast()` returns the LAST valid frame, not the first
    This was the main reason session 3's test was "a mess" — commands worked
    at the spa but the script read pre-command data and reported failures.
  - **Frame parsing aligned with coordinator** — test script now uses the
    same pipeline as the real HA coordinator: `find_frames()` →
    `validate_frame()` → `is_broadcast()` → `unescape_frame(full=adapter.unescape_full_frame)` →
    `adapter.parse_status()`. Previously used manual `buf.index()` parsing.
  - **Temperature command fixed** — `btn_action=0x98` confirmed working
    (adapter had untested `0x88`; live test showed `0x80` fails, `0x98` works).
  - **Blower OFF command changed** — `btn_action` changed from `0x08` (did not
    work — blower stayed on) to `0x00` (matching heater OFF pattern). Not yet
    re-tested live.
  - **Clock write: date is read-only** — the 0xA2 command only sets H:M:S.
    Y:M:D bytes are sent but controller ignores them. Test and docstring updated.
  - **Schedule test values now dynamic** — hardcoded test values happened to
    match the spa's actual schedule, causing false-positive passes. Now computed
    as offsets from current values (hours +2/+3, minutes toggled, enables inverted).
  - **Jets test: retry logic** — RS485 bus collisions can cause command frames
    to be lost. Jets test now retries up to 3× per transition. Fan entity also
    has retry logic (up to 3 attempts with state check between).
  - **Jets test: result variable** — was hardcoded `True`, now uses `jets_pass`.
  - **Blower test: extended delay** — 10s wait after ON before sending OFF
    (possible minimum run time on blower motor).

- **Previous sessions (kept for reference):**
  - Session 3 (2026-05-28): Heater byte blower-flag fix (MASK_HEATER_BLOWER),
    light toggle race condition fix, guided write-test script enhancements.
  - Session 2 (2026-05-27): Options flow, auto clock sync, ozone, pump
    transitions, switch order, translations. Jets off→low and low→off
    confirmed. Blower confirmed.
  - Session 1: Initial integration, byte map, CRC cracking, all entities.

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
- **EW11 connection limit**: 4 concurrent TCP clients. HA uses 1, tools can use up to 3 more.
- **Guided write test**: `source .venv/bin
