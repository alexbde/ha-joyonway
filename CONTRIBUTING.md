# Contributing to ha-joyonway

Thank you for your interest in contributing! This is a community project and all contributions are welcome.

## Getting started

```zsh
git clone https://github.com/alexbde/ha-joyonway.git
cd ha-joyonway
python3.13 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
```

## Running the tests

```zsh
# Unit tests (fast, no hardware required)
.venv/bin/pytest -q -W ignore

# Dry-run simulation suite (no hardware required)
.venv/bin/python tests/live/test_spa_controls.py --non-interactive

# Live tests against physical hardware (requires .env with SPA_BRIDGE_HOST / SPA_BRIDGE_PORT)
.venv/bin/python tests/live/test_spa_controls.py
```

All unit tests and the dry-run suite must pass before a PR can be merged.

## Code style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```zsh
.venv/bin/pip install ruff
.venv/bin/ruff check custom_components/joyonway/
.venv/bin/ruff format custom_components/joyonway/
```

## Adding a model adapter

New controller models are supported by adding an adapter under `custom_components/joyonway/adapters/`.

1. Create `custom_components/joyonway/adapters/<model_id>.py` (e.g. `p23b32.py`).
2. Implement the `ModelAdapter` interface from `adapters/base.py`:
   - `parse_status(frame: bytes) -> dict | None`
   - `entity_descriptions() -> list[SpaEntityDescription]`
   - `build_command(...) -> bytes`
3. Register the adapter in `adapters/__init__.py`'s `get_adapter()` factory.
4. Add entity translations for each new entity key to all locale files in `translations/`.

Use [P25B85Adapter](custom_components/joyonway/adapters/p25b85.py) as a reference implementation and
[docs/protocol.md](docs/protocol.md) for RS485 framing details.

## How to Reverse Engineer & Capture Telemetry

If you have a different Joyonway spa controller model and want to map its protocol or add support for its features, you can capture and share telemetry:

### 1. Expose Raw Protocol Telemetry in HA
Go to the **Joyonway Spa** integration in Home Assistant, click **Entities**, and enable the diagnostic sensors:
- **Heater byte (raw)**
- **Pump byte (raw)**
- **Ozone mode byte (raw)**
- **Activity byte (raw)**
- **Light/cycle byte (raw)**
- **Frame length**
- **Unmapped bytes hash**

Perform actions on your physical spa touchpad (e.g. click jets, lights, or adjust thermostat) and note down which raw bytes change.

### 2. Run the Developer Broadcast Byte Capture Tool
If you have terminal access, you can run our standalone developer utility to analyze unmapped registers in real time:

```zsh
# Run the analysis tool to capture 20 frames directly
SPA_BRIDGE_HOST="192.168.1.150" SPA_BRIDGE_PORT="8899" python3 tools/capture_unmapped_bytes.py --count 20
```

The tool will parse your controller's unmapped bytes and print a clean breakdown showing which byte positions are static vs. changing, their observed values, and your unique MD5 `unmapped_bytes_hash`.

### 3. Run the Live Verification Suite
To see what works on your hardware and what might need adjustments, you can run the live test suite in either simulation/dry-run mode or directly against your hardware:

```zsh
# Run the verification suite offline in simulation mode
python3 tests/live/test_spa_controls.py --dry-run

# Run directly against your physical hardware (bridge host/port configured in .env)
python3 tests/live/test_spa_controls.py
```

Please share these details, your test results, your spa model, and touchpad model on our community thread!

## Architecture & Design Decisions

Below are the core architectural patterns and design decisions implemented in this integration:

- **Persistent TCP connection:** Single shared socket for reads and writes. A background reader loop continuously parses broadcast frames (~1–2s updates). Commands are sent under a write lock.
- **RS485 Sync Frame Pacing:** To prevent RS485 bus collision issues, write commands wait for the idle sync frame (`b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d"`), sleep for 30ms, and then write command frames to hit the quiet bus window precisely.
- **Intent queue:** All entity writes are routed through `IntentQueue` on the coordinator. Merges same-group edits (e.g. multiple schedule slots) within a 300ms coalesce window, cancels redundant commands, and paces commands sequentially to prevent bus contention.
- **Optimistic state & Non-silent snap-back:** All writable entities set pending state immediately on command send. Cleared only when broadcast confirms the new value. If not confirmed in 10s, it snaps back to coordinator state and logs a `WARNING` identifying the entity and failed action.
- **Grace-mode availability:** Entities stay available for 10s after disconnect to avoid UI flicker on brief interruptions (propagated by `JoyonwayCoordinatorEntity`).
- **Strict connectivity diagnostic:** `bridge_connectivity` uses raw TCP state (`coordinator.is_connected`), not grace-mode.
- **Schedule command split:** Schedule writes use two flag modes: `write_mode="state"` for enables (`0xAA/0x62/0x9A/0x52`) and `write_mode="time"` for time edits (`0xAA/0x6A/0x9A/0x5A`). Prevents write refusal issues when slot 2 is disabled.
- **Ozone control & Heater control availability:** Both Ozone and Heater main switches are linked to their respective configuration switches. SpaOzoneSwitch and SpaHeaterSwitch are only available when the corresponding config switch (SpaManualOzoneSwitch / SpaManualHeaterSwitch) is enabled (meaning the spa is in Manual mode).
- **Auto clock sync:** Drift-triggered (>30s) sync with a 1-hour cooldown (cooldown applies to both success and failure to prevent log spam), managed via a standard native CONFIG switch entity.
- **Diagnostics support:** Added entry diagnostics via `diagnostics.py` to redact sensitive fields (IP/Port) and export raw byte states (`heater_byte_raw`, `pump_byte_raw`, etc.) for easier troubleshooting.

## Submitting a PR

- Keep changes focused — one concern per PR.
- Include a test for any new behaviour if feasible.
- Fill in the PR template (especially the testing section).
- Reference any related issue with `Closes #N`.

## Community

General discussion, reverse-engineering findings, and setup help live in the
[Home Assistant Community Thread](https://community.home-assistant.io/t/joyonway-spa-control/582344).
