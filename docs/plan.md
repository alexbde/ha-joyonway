# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller, with a model adapter interface ready for future multi-model expansion.
> **Repository:** [alexbde/ha-joyonway](https://github.com/alexbde/ha-joyonway)
> **Integration Domain:** `joyonway`
> **Status:** Transparent icons implemented, repository migrated, and diagnostics fully complete.

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
  - [ ] Create GitHub release with changelog details.

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
