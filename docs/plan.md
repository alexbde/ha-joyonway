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
> **Status:** All entities implemented. Needs live testing at spa.

> **Documentation policy:** `docs/protocol.md` is the canonical protocol spec.
> This `docs/plan.md` is progress/handoff only.

## 0. AI Instructions

- **No PII / timestamps in code.** Do NOT add dates, author names, usernames,
  IP addresses, or any data that could identify the developer or when work was
  done. Dates belong only in this plan file and in git history — never in
  `.py`, `.json`, or other shipped files.
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
- **UART:** 38400 8N1
- **Pump:** ONE dual-speed (low = filtration, high = massage jets, 20-min auto-off)
- **Light:** RGB LED, 9 states cycling via button
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone port:** Connector on PCB ("Ozonauslass"), byte 14=0x41 is a
  **scheduled disinfection cycle** state (not a separate UV device).
  Cannot be manually toggled from PB554 — runs on schedule only.
- **Blower:** air blower, connector on PCB, button on PB554 panel. Captured and
  implemented as switch. Broadcast state: byte[14] bit 3, byte[28] bit 3.

## 2. Protocol Summary

### Framing

- 38400 8N1, start `0x1A`, end `0x1D`
- Pseudo-escape: `0x1B XX` sequences (see escape table in code)
- P25B85: full-frame unescape. P23B32: tail-only (bytes 55+).
- Frame boundaries detected on raw bytes FIRST, then unescape applied.

### Broadcast byte map (P25B85, logical frame after unescape)

| Byte | Content |
|------|---------|
| 8 | Model ID (`0x03` = P25B85) |
| **9** | Water temperature (°F) |
| **12** | Pump status (`0x02`=low, `0x04`=high) |
| **14** | Heater/blower state (see below) |
| **16** | Setpoint (°F) |
| **17** | Light flags (bit 0 = light ON) |
| **19** | Schedule config (changes on heat/filter schedule writes) |
| **28** | Activity flags (bit 3=blower, bit 5=activity/disinfection) |
| **29** | Filter schedule config (changed 0x4C→0xCD on filter schedule write) |
| 53–58 | Date/time (year, month, day, hour, minute, second) |

**Byte 14 values:**
- `0x40` = off, `0x50` = circulation, `0x55`/`0x54` = heating
- `0x41`/`0xC1` = disinfection, `0x58` = blower active (0x50 + bit 3)

### Command frame types (byte[4] distinguishes type)

| byte[4] | Type | Description |
|---------|------|-------------|
| 0xA1 | Button command | Light/pump/heater/blower (22 bytes) |
| 0xA2 | DateTime set | Set spa clock (22 bytes) |
| 0xA3 | Heat schedule | Program heating time slots (22 bytes) |
| 0xA4 | Filter schedule | Program filtration time slots (22 bytes) |

### Command-byte notes (important)

- Same-session CRC captures (`tools/captures_crc/crc_session.json`) show:
  - Light toggle: byte[9]=0x40, byte[10]=0x58
  - Heater ON/OFF: byte[9]=0x08, byte[10]=0x08/0x00
  - Blower ON/OFF: byte[9]=0x04, byte[10]=0x04/0x00
  - Pump transitions: byte[9]=0x00, byte[10]=0x08; transition encoded in bytes 7-8
- Current integration runtime still uses legacy replay variants for light/blower
  from `adapters/p25b85.py` (documented explicitly in `docs/protocol.md`).

Temperature: 31-frame lookup table currently used in runtime
(`TEMP_COMMAND_TABLE`, 10-40°C).

### CRC — CRACKED ✅

- **Algorithm:** CRC-32 (0x04C11DB7), non-reflected, init=0, xor_out=0x552D22C8
- **Preprocessing:** 32-bit word byte-swap of payload before CRC
- **Storage:** little-endian at payload bytes 16–19
- **Implementation:** `protocol.py` → `compute_crc()` and `build_frame()`
- **Verification:** 21/21 unique same-session frames, all command types
- Dynamic generation is implemented in `protocol.py`, but runtime entity writes
  remain replay/lookup (is-state) until live migration.

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
├── switch.py            # light, heater, blower (on/off via replay)
├── fan.py               # jets (off/low/high via preset_modes)
├── climate.py           # thermostat with debounced slider
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), command frames, temp table
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Entities

| Entity | Platform | What it does |
|--------|----------|--------------|
| **Thermostat** | climate | Water temp + setpoint + heater state; slider with 1.5s debounce |
| **Light** | switch | On/off via toggle replay (state guard: refuses when unknown) |
| **Heater** | switch | On/off via distinct replay frames |
| **Blower** | switch | On/off via distinct replay frames; byte[28] bit 3 = state |
| **Jets** (Düsen) | fan | Off/low/high via preset_modes; handles multi-step transitions |
| **Water temperature** | sensor | Integer °C for history/graphs |
| **Heater state** | sensor | Enum: off / circulation / heating / disinfection / unknown |
| **Pump state** | sensor | Enum: off / low / high |
| **RS485 bridge** | binary_sensor | TCP connectivity |
| Spa clock | sensor | Diagnostic timestamp (disabled by default) |
| Raw pump byte | sensor | Diagnostic (disabled by default) |
| Raw heater byte | sensor | Diagnostic (disabled by default) |

### Key design decisions

- **Fan = "Jets" / "Düsen"** — matches spa manual terminology
- **Enum sensors with translated states** — `heater_state` and `pump_state` with `device_class="enum"`
- **Light toggle safety**: same frame for on/off; switch refuses toggle when state is unknown
- **Heater/blower switches**: distinct ON/OFF frames (not toggles); safe to send
- **Climate debounce**: 1.5s coalescing for slider drags
- **Coordinator write pacing**: global 1.0s command cooldown
- **Pump state machine**: OFF→low→high→OFF cycle; fan handles multi-step transitions
- **Temperatures as integers** — spa only shows whole °C
- **Climate hvac_action**: heating→HEATING, circulation→PREHEATING, off/disinfection→IDLE
- **Blower state**: read from byte[28] bit 3 (MASK_BLOWER = 0x08)
- **Not available on PB554**: disinfection manual toggle, filtration manual toggle, frost protection
- **Screen flip**: handled locally by PB554 panel, not sent on RS485 bus

## 4. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Capture tools | ✅ Done | `guided_capture_38400.py`, `frame_parser_38400.py` |
| 2. Integration | ✅ Done | Deployed, reading live data, HACS install works |
| 3. Validate byte map | ✅ Done | All byte positions confirmed from captures |
| 4. Write commands | ✅ Done | Light + pump + temp replay frames captured |
| 5. Extended captures | ✅ Done | Heater, blower, datetime, filter/heat schedule all captured |
| 6. Temperature control | ✅ Done | Climate with debounced slider, 31-frame lookup |
| 7. Live test writes | **Next** | Test all write entities at spa |
| 8. Schedule/datetime entities | Planned | Heat schedule, filter schedule, datetime sync |
| 9. CRC cracking | ✅ **Done** | P=0x04C11DB7, word32-swap, verified 21/21 frames |
| 10. Polish & release | Planned | After live test |

## 5. Next Steps

### Priority 1: Live testing
1. **Restart HA** with updated integration
2. **Test each entity**: light switch, heater switch, blower switch, jets fan, thermostat slider
3. **Verify blower state** reads correctly from byte[28] bit 3
4. **Check cross-session replay** — heater/blower commands were captured in a different
   session from Phase 4 pump/light commands; confirm they still work

### Priority 2: Dynamic frame generation (CRC cracked!)
With the CRC cracked, we can now:
- Generate temperature commands for ANY setpoint (no lookup table limitation)
- Generate datetime sync frames with current timestamp
- Generate custom schedule frames
- Eliminate the 31-frame `TEMP_COMMAND_TABLE` and compute on the fly
- Implementation: `protocol.build_frame(payload)` computes CRC and escapes
- Important: keep this as a migration task; current runtime is intentionally
  replay/lookup until live validation confirms behavior.

### Priority 3: Polish & release
- Version bump, README final review, HACS release
- PR to frame-analyzer — add P25B85 preset to christopheknap's tool

### Priority 4: Schedule/DateTime entities (Phase 8 — CRC enables this)
CRC is cracked → we can generate frames dynamically for these features:
- **DateTime sync** (0xA2): auto-sync spa clock to HA time on startup/daily.
  Need to decode exact byte encoding (verify with 2–3 captures at known times).
  Implement as `button` or `service` entity (`spa.sync_clock`).
- **Heat schedule** (0xA3): set heating time windows from HA.
  Payload structure captured; implement as `time` entities or service.
  Could expose start/end times for 2 heating slots.
- **Filter schedule** (0xA4): set filtration time windows from HA.
  Same structure as heat schedule; 2 filtration slots.
  Could expose as `time` entities or service.
- **Dynamic temperature** — replace `TEMP_COMMAND_TABLE` lookup with
  `protocol.build_frame()` for any °F target. Eliminates the 31-frame limit.


## 6. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **Restart required** after any code change to the integration.
- **Tests** run with `python3 -m unittest discover -s tests` (97 tests, <1ms).
- **Protocol docs**: `docs/protocol.md` — full protocol reference with all
  captured frame examples, CRC algorithm, byte maps, and payload layouts.
- **CRC implementation**: `protocol.py` → `compute_crc()`, `build_frame()`,
  `pseudo_escape()`. Verified 21/21 frames.
- **Temperature lookup table**: `TEMP_COMMAND_TABLE` in `adapters/p25b85.py`
  still used for replay. Can be replaced with `build_frame()` once live-tested.
  Byte 10 varies by session (0x80/0x98/0x99) — needs live test to confirm
  which value the controller accepts.
- **Command send pattern**: coordinator opens TCP, writes frame, closes.
  Uses `asyncio.Lock` + global 1.0s cooldown to prevent concurrent/burst sends.
- **Entity unique_id for fan** changed from `_pump` to `_jets` — existing HA
  installs may need entity re-registration after update.
- **CRC cracking tools** (in `tools/`):
  - `capture_crc_session.py` — captures all command types in a single session
  - `bf_poly.c` — C brute-force across 2^32 polynomials (409s exhaustive search)
  - `verify_crc32_v2.py` — verifies polynomial + word-swap against all frames
  - `verify_protocol_crc.py` — verifies `protocol.py` implementation
  - Various analysis scripts: `analyze_crc_session2.py`, `extract_poly.py`, `crack_crc2.py`

