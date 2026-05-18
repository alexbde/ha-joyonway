# Joyonway Spa Integration Plan — P25B85 RS485 Controller

> **Goal:** A Home Assistant integration for the Joyonway P25B85 controller,
> with a model adapter interface ready for future multi-model expansion.
>
> **Repo:** `alexbde/ha-joyonway-p25b85` — independent from upstream
> **Upstream:** christopheknap keeps `ha-joyonway-p23b32` P23B32-only (decided
> 2026-05-18). We removed `joyonway_p23b32` from our repo (HACS requires single
> integration per repo). His code remains at
> https://github.com/KnapTheBuilder/ha-joyonway-p23b32 for reference.
> Multi-model umbrella revisit in ~6 months.
>
> **Integration domain:** `joyonway_p25b85`
> **Hardware:** P25B85 + PB554 + Elfin EW11
> **Status:** Phase 2 complete, integration deployed to HA and reading live data.

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

| Byte | Content | Validated? |
|------|---------|-----------|
| 8 | Model ID (`0x03` = P25B85) | ✅ confirmed from live data |
| **9** | Water temperature (°F) | ✅ live: shows correct °C |
| **12** | Pump status (current adapter uses this) | ⚠️ UNCONFIRMED: could be 12 or 13 |
| **15** | Heater state (0x00/0x50/0x54/0x40/0xC1) | ⚠️ needs capture with heater active |
| **16** | Setpoint (°F) | ✅ live: shows correct °C |
| **18** | Light flags (bit 0 = light ON) | ⚠️ needs capture with light on |
| **29** | UV/Ozone flag (`0x20` when active) | ⚠️ needs capture |
| 53–58 | Date/time | low priority |

### Key unknown: pump byte 12 vs 13

KDy reference has `0x04` at byte 12 and `0xF5` at byte 13. Adapter currently
uses byte 12 with masks `0x02` (low) and `0x04` (high). Must diff captures
with pump on/off to confirm.

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

### Live-validated (2026-05-18)

- ✅ Integration installs via HACS custom repo
- ✅ Config flow connects to EW11 and creates device
- ✅ Water temperature reads correctly (33.9°C observed)
- ✅ Setpoint reads correctly (37.8°C observed)
- ✅ All entities appear in HA with correct names
- ✅ German/English/French translations work
- ✅ Bridge connectivity sensor works

### Not yet validated (needs captures)

- Pump low/high (need to press pump button and diff)
- Light on/off
- Heater state transitions
- Ozone/UV activation

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
| 1. Capture tools | ✅ Done | `guided_capture_38400.py`, `frame_parser_38400.py`, 94 tests passing |
| 2. Integration | ✅ Done | Deployed, reading live data, HACS install works |
| 3. Validate byte map | 🔜 Next | Need spa visit for pump/light/heater captures |
| 4. Write commands | Planned | Only after Phase 3; replay-only, no synthetic frames |
| 5. Polish & release | Planned | After Phase 4 |

---

## 6. Next Steps

1. **Go to spa** — run guided capture, do all actions
2. **Analyze** — `frame_parser_38400.py --diff`, confirm pump byte index
3. **Update adapter** — fix `IDX_PUMP_BYTE` if needed, confirm all masks
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
- **Heater active vs heater state**: `heater_active` = binary (byte 15 == 0x54
  only), `heater_state` = text sensor showing full cycle stage.
- **Tests** run with `python3 -m unittest discover -s tests` (no pytest needed,
  pure stdlib). 94 tests, <1ms.
- **christopheknap's frame-analyzer**: https://github.com/KnapTheBuilder/joyonway-frame-analyzer
  — browser tool, accepts xxd hex dumps. Open to P25B85 preset PR.
  Also has `docs/PROTOCOL.md` as shared protocol reference.
