# AGENTS.md

This file contains instructions, coding conventions, architecture boundaries, and learned constraints for AI coding agents (Gemini, Claude, GPT, etc.) working on the `ha-joyonway` repository.

---

## 1. Project Context & Objectives

- **Domain**: `joyonway` (Home Assistant integration)
- **Purpose**: Local-push integration for Joyonway spa controllers over RS485 via a TCP bridge (operating in TCP Server mode, e.g. Elfin EW11).
- **Core Architecture**: Modular adapter pattern. Different controller models implement the `ModelAdapter` protocol (`custom_components/joyonway/adapters/base.py`) which defines broadcast frame detection, status parsing, entities, and command builders.
- **Reference Model**: Joyonway P25B85 (PCB `P2325B0003 R05`, PB554 color screen touchpad).
- **Goal**: Maintain 100% test coverage, strict typing, lint-free formatting, and HACS compliance.
- **Protocol Documentation**: The [protocol.md](docs/protocol.md) file is the single source of truth for the RS485 communication protocol structure and register map. You must update this file immediately with any new protocol-level insights or byte definitions uncovered during development.

---

## 2. Coding Guidelines & Style

- **Python Version**: 3.13+ (conforms to Home Assistant requirements)
- **Formatting & Linting**: Strictly adhere to [Ruff](https://docs.astral.sh/ruff/) rules. Run `.venv/bin/ruff check` and `.venv/bin/ruff format` on any code changes.
- **Type Annotations**: Strictly typed. Run `.venv/bin/mypy custom_components/joyonway/` to verify.
- **Coordinators**: All entities must derive from `JoyonwayCoordinatorEntity`, which is generically typed:
  ```python
  class JoyonwayCoordinatorEntity(CoordinatorEntity["JoyonwayP25B85Coordinator"]):
  ```
- **Branding & Icons**: Keep icons simple and clean. Use standard Material Design Icons (`mdi:` prefix) as described in `ModelAdapter.entity_descriptions()`.
- **Response Format**: Propose clear, exact code replacements (diffs) and avoid conversational fluff.

---

## 3. Strict Constraints & Guardrails

- **No PII or Timestamps**: Do **NOT** add dates, author names, usernames, IP addresses, or any metadata that could identify the developer or when the work was done to any python, JSON, or template files. Shipped files must remain anonymous.
- **Safety First**: The integration must **never** send unsolicited command packets or write automatically to the spa on integration startup.
- **RS485 Sync-Frame Pacing (Mitigation)**: The RS485 bus is prone to packet collisions. All write commands **must** wait for the idle sync frame (`b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d"`), sleep for 30ms, and then write command frames to hit the quiet bus window precisely.
- **Intent Queue & Coalescing**: All entity writes are routed through `IntentQueue` on the coordinator. Group edits (like multiple schedule slots) coalesce within a 300ms window, redundant commands (e.g. rapid ON->OFF) cancel out, and writes drain sequentially under lock.
- **Optimistic UI State & Snap-Back**: Writable entities set their pending/optimistic state immediately upon command submission. Revert back to the coordinator's state and log a `WARNING` if the target state is not confirmed by the spa broadcast within 10 seconds.
- **Grace-Mode Availability**: Entities stay `available = True` for 10 seconds after a connection dropout to prevent UI flickering on brief WiFi dropouts.
- **Strict Connectivity Diagnostic**: The `bridge_connectivity` sensor must use the raw TCP connection state (`coordinator.is_connected`), bypassing the 10-second grace window.
- **Ozone & Heater Availability Lock**: Main `Ozone` and `Heater` switch entities are linked to their config switches (`Manual Ozone`, `Manual Heating`). They are unavailable (`available = False`) unless the corresponding config switch is enabled (meaning the spa is in Manual mode).
- **Auto Clock Sync**: Clock sync is drift-triggered (>30s) and runs with a 1-hour cooldown (applying to both success and failure to prevent log spam), managed via a native CONFIG switch.
- **Schedule Command Flags**: P25B85 schedule writes use two flag modes: `write_mode="state"` for enables (`0xAA/0x62/0x9A/0x52`) and `write_mode="time"` for time edits (`0xAA/0x6A/0x9A/0x5A`). The `0x6A`/`0x5A` force-write flags are critical to prevent write refusal issues when slot 2 is disabled.
- **Diagnostics Redaction**: Config entry diagnostics (`diagnostics.py`) must redact sensitive keys (like `host`, `port`) and export raw byte states (`heater_byte_raw`, `pump_byte_raw`, etc.) for troubleshooting.

---

## 4. Testing Requirements

- **Unit Tests**: Run `.venv/bin/pytest -q -W ignore` to execute unit tests. All tests must pass.
- **Dry-Run Simulation**: Run `.venv/bin/python tests/live/test_spa_controls.py --non-interactive` to simulate the full logical control path. 64 tests must pass cleanly.
- **Physical Test Restorations**: When running physical hardware tests, always include a `finally` block to restore the initial state (schedules, clock settings, heater status) to avoid leaving the spa in a weird state.

---

## 5. Reverse Engineering Guided Capture Runbook

When investigating unknown byte behavior, create an **interactive guided capture script** rather than asking the user to manually operate the spa.

1. **Define a runbook**: A numbered sequence of actions (e.g. "enable blower") with parsed state triggers.
2. **Build a script**:
   - Connect to the bridge via a TCP socket.
   - Import the protocol parser and adapter.
   - Display a live one-line status showing parsed states.
   - Guide the user with prompts (`[STEP N/M] instruction`).
   - Automatically detect step transitions by monitoring parsed states and raw bytes.
   - Save raw bytes to a `.bin` file in `tools/captures/` for later analysis.
3. Use `socket.setblocking(False)` with a polling loop for simplicity in standalone scripts.
4. Carriage return `\r` for live updating without scroll.
5. Warn user if no data is received for 15s (stale connection).
