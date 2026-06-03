# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller, with a model adapter interface ready for future multi-model expansion.
> **Repository:** [alexbde/ha-joyonway](https://github.com/alexbde/ha-joyonway)
> **Integration Domain:** `joyonway`
> **Status:** Transparent icons implemented, repository migrated, and diagnostics fully complete.

---

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

### 0.1 Guided Capture Scripts

When investigating unknown byte behavior or verifying protocol assumptions, create an **interactive guided capture script** rather than asking the user to manually operate the spa and describe what they see. This produces deterministic, parseable evidence.

**General mechanism:**

1. **Define a runbook** — a numbered sequence of physical actions (e.g., "enable heater", "set jets to low") with clear transition triggers (byte values or parsed state changes that indicate the action was completed).
2. **Build a script** that:
   - Connects to the bridge via TCP socket (using `.env` for host/port)
   - Imports the protocol parser and adapter from `custom_components/` to parse frames in real-time
   - Displays a live one-line status showing all relevant byte values (hex) and parsed states
   - Guides the user through each step with clear prompts (`[STEP N/M] instruction`)
   - Automatically detects step transitions by monitoring parsed state changes
   - Captures steady-state data (e.g., `time.sleep(5)` after a transition) when needed
   - Writes all raw bytes to a `.bin` file in `tools/captures/` for later analysis
3. **Step transition logic** should check parsed byte values directly (e.g., `p_raw == 0x02`) in addition to derived state strings (e.g., `jets == "low"`) for robustness.
4. **Analysis scripts** can then parse the `.bin` capture file to produce state transition tables, proving or disproving assumptions about byte semantics.

**Template:** See [guided_capture.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tools/guided_capture.py) for the reference implementation.

**Key design rules:**
- Use `socket.setblocking(False)` with a polling loop (not asyncio) for simplicity in standalone scripts
- Print status with `\r` carriage return for live updating without scroll
- Detect stale connections (no data for 15s) and warn the user
- Always save raw bytes — they can be re-parsed with different logic later

---

## 1. Open Todos
 
### 1.1 Remaining Live Verification
We have consolidated and replaced the old schedule runner with a comprehensive, unified live verification suite at [test_spa_controls.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/live/test_spa_controls.py). This suite covers all basic controls, complete schedule matrices, ozone controls, clock drift/auto-sync, intent queue coalescing/cooldowns, and socket drop resilience.

To complete the physical live testing at the spa hardware:
- [ ] **Run the Unified Live Verification Suite on Physical Hardware:**
  - Configure the `.env` file at the root with `SPA_BRIDGE_HOST` and `SPA_BRIDGE_PORT`.
  - Activate the virtual environment (`source .venv/bin/activate`).
  - Run the suite: `python tests/live/test_spa_controls.py`.
  - Select option `0` to execute all tests, or target specific numbers (1 through 6) to run individual suites.
  - Review the generated JSONL log files and raw binary captures inside `tests/live/artifacts_schedule_matrix/`.

---

### 1.2 Polish & Release
- [ ] **Code Polish & Verification:**
  - [ ] Audit imports & dead code (Ruff check/format).
  - [ ] Validate type annotations (`mypy --strict` on core files).
  - [ ] Review module/class docstrings across integration modules.
  - [ ] Verify log formatting consistency (`"Entity: action"` prefix, no PII).
  - [ ] timing constantsTiming/cooldown review.
- [ ] **HACS Compliance & Packaging:**
  - [ ] Update `manifest.json` version (`0.1.0` -> `1.0.0`) and iot_class (`local_push`).
  - [ ] Update `hacs.json` with minimum HA version metadata.
  - [ ] Verify translation files completeness (`en.json`, `de.json`, `fr.json`).
- [ ] **Documentation:**
  - [ ] Create `CHANGELOG.md` with 1.0.0 release log.
  - [ ] Update `README.md` status, installation links, and compatibility tables.
- [ ] **GitHub Repository Best Practices:**
  - [ ] Add GitHub issue templates (`.github/ISSUE_TEMPLATE/` for bug reports, feature requests).
  - [ ] Add a Pull Request template (`.github/pull_request_template.md`).
  - [ ] Add contributing guide and repository policies (`CONTRIBUTING.md`, `SECURITY.md`).
- [ ] **Release tagging:**
  - [ ] Run final unit test suite (`pytest`).
  - [ ] Git commit release configuration, tag the release, and push tags.
  - [ ] Create GitHub release with full release notes/changelog body text in markdown, so HACS can display the changes directly in the HA Update UI instead of a simple external link.

---

## 2. Key Architecture & Design Decisions

- **Persistent TCP connection:** Single shared socket for reads and writes. Background reader loop continuously parses broadcast frames (~1–2s updates). Commands sent under a write lock.
- **Grace-mode availability:** Entities stay available for 10s after disconnect to avoid UI flicker on brief interruptions (propagated by `JoyonwayCoordinatorEntity`).
- **Strict connectivity diagnostic:** `bridge_connectivity` uses raw TCP state (`coordinator.is_connected`), not grace-mode.
- **Optimistic state & Non-silent snap-back:** All writable entities set pending state immediately on command send. Cleared only when broadcast confirms the new value. If not confirmed in 10s, it snaps back and logs a `WARNING` identifying the entity and failed action.
- **Intent queue:** All entity writes are routed through `IntentQueue` on the coordinator. Merges same-group edits (e.g., multiple schedule slots) within a 300ms coalesce window, cancels redundant commands, and paces commands with a 1.0s cooldown to prevent bus contention.
- **Schedule command split:** Schedule writes use two flag modes: `write_mode="state"` for enables (`0xAA/0x62/0x9A/0x52`) and `write_mode="time"` for time edits (`0xAA/0x6A/0x9A/0x5A`). Prevents write refusal issues when slot 2 is disabled.
- **Ozone control:** Mode set via options flow, synced from broadcast byte 13. Ozone switch is created *only* in Manual mode.
- **Auto clock sync:** Drift-triggered (>30s) sync with a 1-hour cooldown (cooldown applies to both success and failure to prevent log spam).
- **Diagnostics support:** Added entry diagnostics via `diagnostics.py` to redact sensitive fields (IP/Port) and export raw byte states (`heater_byte_raw`, `pump_byte_raw`, etc.) for easier troubleshooting.

---

## 3. Reference Information

### Hardware Configuration
- **Spa:** Home Deluxe White Marble
- **Controller:** Joyonway P25B85 (PCB `P2325B0003 R05`)
- **Touchpad:** PB554 color screen
- **Bridge:** Elfin EW11 (RS-485 to WiFi TCP server, port 8899)
- **Pump:** One dual-speed (low = filtration, high = massage jets, 20-min auto-off)
- **Light:** RGB LED (9 states cycled locally; RS485 simple toggle)
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone:** Manual & Auto modes supported

---

## 4. Completed Milestones

- **Phases 1–18 (May 2026):** Scaffolding, unescaping protocol parser, dynamic commands, schedule support, options flow, and UI optimistic states.
- **Phase 19 (May 2026):** Verified schedule writes live, resolving the disabled slot 2 write refusal using the `0x5A` flags broadcast.
- **Phase 20–21 (May 2026):** Added schedule UI matrix automated live test (`tests/live/test_schedule_ui_matrix.py`) confirming all 50 test cases pass.
- **Phase 22–23 (May 2026):** Implemented `IntentQueue` to serialize command writes, prevent command clashes, and support coalescing. Added strict schedule-write error handling.
- **Phase 24 (June 2026):** Code review enhancements, including options flow refactor, exclusive `entry.runtime_data` usage, sequential multi-frame parsing, and config entry diagnostics support.
- **Phase 25 (June 2026):** Sniffed the about/version screens and proved that version info is managed locally by the touchpad's own flash, not broadcasted over RS485.
- **Phase 26 (June 2026):** Replaced branding icons with clean, transparent-background SVGs/PNGs (halo-free matting).
- **Phase 27 (June 2026):** Replaced the single-purpose `test_schedule_ui_matrix.py` with a highly optimized, Unified Live Verification Suite at [test_spa_controls.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/live/test_spa_controls.py) to validate all logical control paths (basic controls, schedule matrix, ozone controls, clock sync, intent queue coalescing/cooldown, and connection drop resilience). Optimized all delay and timeout variables for `--dry-run` simulation mode to achieve sub-20 second suite runs, achieving 100% dry-run pass rate (57/57 tests). Updated live test documentation in [README.md](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/live/README.md).
- **Phase 28 (June 2026):** Resolved physical hardware testing failures and cleanup issues. Implemented a baseline state capture and recovery block in `finally` to restore components (including schedules and heater switches) to their initial states. Added safety pacing with a 12.0s jets settling delay. Implemented an ozone OFF fallback command (`0x00`) to address manual ozone OFF issues. Split datetime tests into separate time-only (`0x50`) and date+time (`0x05`) write validations. Achieved 100% offline simulation pass rate (61/61 tests).
- **Phase 29 (June 2026):** Fixed physical hardware test failures for setpoint adjust and date/time sync. Added `attempt_with_retries` wrapper to the temperature setpoint change and restoration commands to handle RS485 packet collision flakiness. Discovered and fixed three root causes in `test_set_datetime`: (1) the `0x50` time command was sent with today's local date instead of the spa's current internal date — the hardware silently rejects writes where the date fields don't match its internal date; (2) the `_is_target_time` convergence check compared full datetimes, so a date mismatch produced a multi-day delta that always exceeded the 15s threshold even when the time was correctly applied; (3) the `0x05` date command used stale time fields from before step 1 changed the spa's time — the hardware also rejects date writes where the time fields don't match its current internal time. Added `build_time_command` and `build_date_command` convenience wrappers to `P25B85Adapter` to make call sites explicit. Clarified the timezone contract in `coordinator.py`: the spa stores raw local time, the adapter tags `spa_datetime` with `dt_util.DEFAULT_TIME_ZONE`, and clock sync correctly writes `dt_util.now().hour/minute/second` (local time). All three sub-tests verified passing on physical hardware (3/3 PASS).
- **Phase 30 (June 2026):** Implemented an adaptive, broadcast-counting State Verification and Retry Loop inside `IntentQueue` (up to 3 attempts, waiting up to 2 broadcasts per attempt, with a 4.0s safety timeout and failure callback to immediately clear optimistic state). Aligned all entity `overrides` dictionary keys with coordinator broadcast data keys across the light, heater, blower, ozone, and setpoint controls. Overrode `async_set_updated_data` in the coordinator to notify custom callbacks instead of using Home Assistant's `async_add_listener` (avoiding issues with `FakeEntry` missing `pref_disable_polling`). Refactored unit tests and the dry-run test suite (fixing decoupled heater/ozone simulation states and adding a `--non-interactive` mode). Integrated the dry-run verification suite as a step in the GitHub Actions `tests.yml` CI workflow. All 131 unit tests and 61 dry-run simulation tests pass successfully.
- **Phase 31 (June 2026):** Guided capture at the physical spa proved byte 12 (pump) exclusively represents manual jets, completely independent of automatic circulation (byte 14). During circulation (`h=0x51`), pump byte stays `0x00`; manual jets produce `0x02` (low) or `0x04` (high) regardless of heating/circulation state. Removed the unnecessary `_verify_jets` special-case function — the default `_default_verify` (`data["jets"] == target`) is sufficient. The original jets command failures were caused by (a) the `_submit_pump_intent` no-op check comparing against live state instead of pending state, and (b) the `_build_pump` builder having a redundant no-op check. Both were fixed in Phase 30. Created [guided_capture.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tools/guided_capture.py) — a reusable interactive capture tool.
- **Phase 32 (June 2026):** Added speed percentage control support (`FanEntityFeature.SET_SPEED`, `percentage`, `async_set_percentage`) to `SpaPumpFan` (jets) to resolve UI card control issues (e.g. vertical sliders calling percentage service instead of preset controls). Verified direct transitions (`off -> low -> off -> high -> off`) in the live test suite (`test_spa_controls.py`) and updated the unit tests. All 132 pytest unit tests and 62 dry-run simulation tests passed successfully.


