# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller,
> with a model adapter interface ready for future multi-model expansion.
>
> **Repo:** `alexbde/ha-joyonway-p25b85` — independent from upstream
> **Upstream:** christopheknap keeps `ha-joyonway-p23b32` P23B32-only.
> We removed `joyonway_p23b32` from our repo (HACS requires single
> integration per repo). His code remains at
> https://github.com/KnapTheBuilder/ha-joyonway-p23b32 for reference.
> Multi-model umbrella revisit in ~6 months.
>
> **Integration domain:** `joyonway_p25b85`
> **Hardware:** P25B85 + PB554 + Elfin EW11
> **Status:** Phase 3 complete, byte map fully validated from captures.

---

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
  3. Verify the plan file is self-contained — a new AI session with no
     prior context should be able to read it and continue the project.

---

## 1. Hardware

- **Spa:** Home Deluxe White Marble (outdoor whirlpool, rigid/hardshell)
- **Controller:** Joyonway P25B85, PCB `P2325B0003 R05`
- **Touchpad:** PB554 colour screen
- **Bridge:** Elfin EW11, RS-485 → WiFi, TCP server (IP in `.env`, port 8899)
- **UART:** 38400 8N1
- **Pump:** ONE dual-speed (low = filtration, high = massage jets, 20-min auto-off)
- **Light:** RGB LED, 9 states cycling via button
- **Heater:** 2 kW resistive, thermostat-controlled
- **Ozone:** UV lamp on ozonator connector (manual calls it "Ozonauslass")
- **Blower:** connector exists but NOT wired

---

## 2. Protocol Summary

### Framing

- 38400 8N1, start `0x1A`, end `0x1D`
- Pseudo-escape: `0x1B XX` sequences (see escape table in code)
- P25B85: full-frame unescape. P23B32: tail-only (bytes 55+).
- Frame boundaries detected on raw bytes FIRST, then unescape applied.

### Bus cycle (~600ms total, 12 frames)

Broadcast at `0xFF` every cycle = main data source (~16 broadcasts per 10s).

### Broadcast byte map (P25B85, logical frame after unescape)

| Byte | Content | Status |
|------|---------|--------|
| 8 | Model ID (`0x03` = P25B85) | ✅ confirmed |
| **9** | Water temperature (°F) | ✅ confirmed |
| **12** | Pump status (`0x02`=low, `0x04`=high) | ✅ confirmed from captures |
| 13 | Local captures: `0x7D` — static/ignored, not pump data | ✅ confirmed not pump data |
| **14** | Heater state (`0x40`/`0x50`/`0x55`/`0x41`) | ✅ confirmed (was 15) |
| 15 | Always `0x00` — not heater state | ✅ confirmed static |
| **16** | Setpoint (°F) | ✅ confirmed |
| **17** | Light flags (bit 0 = light ON, bit 7 = cycle flag) | ✅ confirmed (was 18) |
| 18 | Always `0x00` | ✅ confirmed static |
| 19 | `0xCB` idle, `0x4B` during heating/UV | observed |
| **27** | Pump mirror (same as byte 12) | ✅ confirmed |
| **28** | Activity flag (`0x20` during heating AND UV; not UV-specific) | ✅ confirmed (was 29) |
| 29 | Local captures: `0x4B` — static/ignored | ✅ confirmed static locally |
| 53–58 | Date/time | ✅ confirmed |

### Heater state values (byte 14, validated)

KDy describes three heating stages: circulation → heating → cooldown

| Value | State | Description | Source |
|-------|-------|-------------|--------|
| `0x40` | cooldown | Post-heating rest / heater idle | ✅ KDy + our captures |
| `0x50` | circulation | Circulation pump running, pre-heat or monitoring | ✅ KDy + our captures |
| `0x54` | heating | Heater element active | KDy (not seen in our captures) |
| `0x55` | heating | Heater element active | ✅ our captures (bit 0 differs from KDy) |
| `0x41` | uv_ozone | UV lamp / ozone cycle | ✅ our captures |
| `0xC1` | uv_ozone | UV lamp / ozone cycle | KDy (bit 7 differs from ours) |

### Corrections from KDy reference

KDy used **1-based byte numbering** in post #74. All positions are off by +1
from 0-based indexing used in the code. KDy's raw data is fully consistent
with our captures once this is corrected:

| KDy (1-based) | 0-based | Field | Match? |
|---|---|---|---|
| byte 13 | byte 12 | pump | ✅ exact |
| byte 15 | byte 14 | heater state | ✅ exact |
| byte 18 | byte 17 | light flags | ✅ exact |
| byte 28 | byte 27 | pump mirror | ✅ exact |
| byte 29 | byte 28 | UV/activity flag | ✅ exact |

Heater state values mostly match. Two values differ by 1 bit (possibly
firmware revision or sub-state):
- Heating: KDy `0x54` vs our `0x55` (bit 0 differs)
- UV/ozone: KDy `0xC1` vs our `0x41` (bit 7 differs)
- Cooldown `0x40` and circulation `0x50` match exactly.
- Both KDy and our values are accepted in the adapter.

**Byte 28** fires during both heating and UV — not UV-specific. UV detection
uses heater state byte 14 (`0x41` or `0xC1`) only. Bytes 13 and 29 are static
in local captures but have different filler values in KDy's sample, so they
should be treated as ignored/static fields rather than model constants.

### CRC safety

- ❌ NEVER send frames with forged CRC (can activate heater)
- ✅ ONLY replay verbatim captured frames from physical panel

---

## 3. Current Implementation

### File structure

```
custom_components/joyonway_p25b85/
├── __init__.py          # entry setup, coordinator creation
├── const.py             # domain, config keys, defaults
├── manifest.json        # HACS-compatible, v0.1.0
├── config_flow.py       # IP + port, TCP connection test
├── protocol.py          # find_frames, pseudo_unescape, validate_frame
├── coordinator.py       # async TCP polling, frame parsing via adapter
├── sensor.py            # adapter-driven (translation_key based)
├── binary_sensor.py     # adapter-driven + bridge connectivity
├── strings.json         # entity translations (base)
├── adapters/
│   ├── __init__.py      # registry: get_adapter("P25B85")
│   ├── base.py          # ModelAdapter protocol + SpaEntityDescription
│   └── p25b85.py        # byte map, parse_status(), entity list
├── brand/
│   ├── icon.png         # 256×256
│   └── icon@2x.png      # 512×512
└── translations/
    ├── en.json
    ├── de.json
    └── fr.json
```

### Entity translations use `translation_key`

Entity names are NOT hardcoded — they come from `translations/*.json` under
the `entity.sensor.*` and `entity.binary_sensor.*` keys. The adapter's
`SpaEntityDescription.key` maps to the translation key.

### Live-validated

- ✅ Integration installs via HACS custom repo
- ✅ Config flow connects to EW11 and creates device
- ✅ Water temperature reads correctly (33.9°C observed)
- ✅ Setpoint reads correctly (37.8°C observed)
- ✅ All entities appear in HA with correct names
- ✅ German/English/French translations work
- ✅ Bridge connectivity sensor works

### Capture-validated

- ✅ Pump low/high
- ✅ Light on/off
- ✅ Heater state transitions (circulation → heating → cooldown)
- ✅ Ozone/UV activation

---

## 4. Capture Plan (next spa visit)

Run: `python3 tools/guided_capture_38400.py` (default 10s per segment, all actions)

**Priority actions (must do):**
1. Pump LOW → press pump once, confirm which byte changes
2. Pump HIGH → press pump again
3. Light ON → any colour

**Nice to have:**
4. Heater → raise setpoint, wait for circulation/heating on display
5. Ozone → if visible on panel (may run automatically)

**Tips:**
- Wait 2-3s after pressing button before starting capture
- For heater: wait until panel shows heating symbol, then capture (may take 30-60s)
- Total time: ~15 minutes for all actions

**After capture:**
```bash
python3 tools/frame_parser_38400.py --diff captures/XX_baseline_before.bin captures/XX_pump_low_active.bin
```
Also drop xxd hex into https://knapthebuilder.github.io/joyonway-frame-analyzer/

---

## 5. Phase Status

| Phase | Status | Notes |
|-------|--------|-------|
| 1. Capture tools | ✅ Done | `guided_capture_38400.py`, `frame_parser_38400.py` |
| 2. Integration | ✅ Done | Deployed, reading live data, HACS install works |
| 3. Validate byte map | ✅ Done | Captures analyzed, 3 byte positions corrected (KDy 1-based → 0-based) |
| 4. Write commands | Planned | Only after Phase 3; replay-only, no synthetic frames |
| 5. Polish & release | Planned | After Phase 4 |

---

## 6. Next Steps

1. ~~**Go to spa**~~ ✅ Done — captured all actions
2. ~~**Analyze**~~ ✅ Done — 3 byte positions corrected (light→17, heater→14, UV→28)
3. ~~**Update adapter**~~ ✅ Done — all IDX values, heater state map, UV logic fixed
4. **PR to frame-analyzer** — add P25B85 preset to christopheknap's tool
5. **Phase 4** — capture command frames from panel for write support

---

## 7. Technical Notes for Next Session

- **`.env` file** holds bridge IP (gitignored). Tools auto-load it.
- **`.env.example`** has generic `192.168.1.100` placeholder.
- **HACS** requires single integration per repo — that's why `joyonway_p23b32`
  was removed (it caused HACS to install to wrong path).
- **Restart required** after any code change to the integration.
- **Entity names** come from `translation_key` + translation files, not from
  `_attr_name`. If adding entities, add keys to all 3 translation files +
  `strings.json`.
- **Ozone** — manual calls it "Ozonauslass". Entity named "Ozon" (DE) / "Ozone"
  (EN/FR). It's a UV lamp plugged into the ozone port.
- **Heater active vs heater state**: `heater_active` = binary (byte 14 in
  `{0x54, 0x55}`), `heater_state` = text sensor showing full cycle stage.
- **KDy byte numbering**: KDy used 1-based numbering in HA post #74.
  All code uses 0-based. Subtract 1 from any KDy reference.
- **Tests** run with `python3 -m unittest discover -s tests` (no pytest needed,
  pure stdlib). 95 tests, <1ms.
- **christopheknap's frame-analyzer**: https://github.com/KnapTheBuilder/joyonway-frame-analyzer
  — browser tool, accepts xxd hex dumps. Open to P25B85 preset PR.
  Also has `docs/PROTOCOL.md` as shared protocol reference.
