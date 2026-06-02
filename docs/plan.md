# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller, with a model adapter interface ready for future multi-model expansion.
> **Repository:** [alexbde/ha-joyonway](https://github.com/alexbde/ha-joyonway)
> **Integration Domain:** `joyonway`
> **Status:** Transparent icons implemented, repository migrated, and diagnostics fully complete.

---

## 1. Open Todos

### 1.1 Remaining Live Verification
We need to test the remaining control paths and resilience features on physical hardware.

1. **Test Ozone Control:**
   * Verify manual on/off toggles in Manual mode.
   * Verify state is reflected correctly on the HA UI and corresponds to the physical panel.
2. **Verify Auto Clock Sync:**
   * Verify the clock sync triggers when time drift exceeds 30 seconds.
   * Confirm the 1-hour cooldown is respected on both success and failure.
3. **Verify Resilient UI & Reconnection:**
   * Test physical connection drops (e.g. EW11 bridge restarts or network drops).
   * Verify the persistent connection reconnects with exponential backoff.
   * Verify optimistic state snap-backs (with 10-second timeout warning logs) function correctly.

> [!TIP]
> **Live Test Script Suggestion:** 
> It makes great sense to write an automated live test script (e.g. [test_resilience_and_control.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/live/test_resilience_and_control.py) or similar) to verify these paths, similar to how [test_schedule_ui_matrix.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/live/test_schedule_ui_matrix.py) validates the schedule. 
> This script could:
> - Temporarily inject a large time offset to force-trigger clock sync.
> - Cycle ozone controls.
> - Temporarily close the TCP connection to verify automatic reconnects and optimistic state timeouts.

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
