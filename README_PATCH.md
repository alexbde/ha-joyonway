# README PATCH v3 — bilingual EN/FR + SAFETY & LIMITATIONS section

## 📍 Où insérer

Dans `README.md`, trouve la ligne `## Dashboard example` (vers ligne ~344).
Insère le contenu ci-dessous **JUSTE AVANT** cette ligne.

Le patch ajoute DEUX sections :
1. `## ⚠️ Safety & Current Limitations` (warnings complets + DO/DON'T table)
2. `## Setpoint command lock (30s timing fix)` (le verrou 30s)

================================================================================
COPIER UNIQUEMENT LE CONTENU ENTRE LES LIGNES ============ DANS README.md
================================================================================

================================ DÉBUT À COPIER ================================

## ⚠️ Safety & Current Limitations

> **Read this before sending any RS485 command to your spa.** This integration is at an early stage of public release and works by replaying captured RS485 frames. The Joyonway controller has very loose validation of incoming frames, which is what makes the integration work — but the same looseness makes the protocol unsafe to brute-force.

### What you must know before using this integration

#### 🚨 The CRC tolerance is a double-edged sword

The controller accepts replayed frames without re-validating their CRC. That is why this integration can work at all: I captured frames from my physical PB555 panel and the controller accepts them when replayed verbatim by Home Assistant.

But [@KDy](https://community.home-assistant.io/u/kdy) reported on the [HA Community thread](https://community.home-assistant.io/t/joyonway-spa-control/582344) that on his P25B85, sending a setpoint frame with an **invalid/arbitrary CRC** unexpectedly **turned the heater ON** — even though it did not change the setpoint as intended. That kind of edge case can cost real money and, depending on the install, present a thermal risk.

#### 🚨 Each controller model has its own frame layout

Confirmed in the forum discussion: the P23B32 (this repo), P25B85 (KDy), P69B133 (@Gaet78), and P20B29 (@Yannickt26) share the same A1/B4 frame skeleton, but **bit positions, byte indices, and pseudo-escape scope vary by model**. A frame captured on a P23B32 is not guaranteed to do the right thing on a different controller, even if the structure looks similar.

#### 🚨 The integration ships with a limited set of captured setpoint frames

At this stage I have captured and validated only 5 setpoint values:

| °C | °F | Script |
|----|----|--------|
| 15 | 59 | `script.spa_cmd_consigne_hiver` |
| 30 | 86 | `script.spa_cmd_consigne_veille` |
| 37 | 99 | `script.spa_cmd_consigne_37` |
| 38 | 100 | `script.spa_cmd_consigne_38` |
| 39 | 102 | `script.spa_cmd_consigne_39` |

A Home Assistant `climate` entity exposes a 15–40 °C slider, but values **not** in the table above have no captured frame behind them. If you wire a climate slider to a script that crafts the missing frames dynamically, you fall straight into the @KDy edge case above. Either:
- only call the 5 validated scripts above, or
- capture the remaining frames yourself from your own physical panel (one capture per °F).

### ✅ Do / ❌ Don't

| ✅ Do | ❌ Don't |
|------|---------|
| Replay frames captured from your own physical panel | Craft frames with random or guessed CRC bytes |
| Build a per-temperature lookup table by capturing one frame per °F | Modify a single byte of a captured frame (e.g. byte 16 = setpoint in °F) without recapturing — the CRC will no longer match |
| Validate every new frame on the spa with eyes on the controller LEDs and a power meter on the heater circuit | Brute-force CRC values to discover the protocol |
| Use the buttons (37 / 38 / 39 / VEILLE / HIVER) which call validated scripts | Drag a climate slider to a value that has no captured frame and assume the controller will reject it — it may not |
| Test new captures with the integration disabled first (controller only sees the panel) | Send commands while the controller is mid-broadcast of a previous state (use the 30 s setpoint lock below) |

### What this integration does NOT do (yet)

- ❌ No CRC computation — frames are byte-identical replays of captures
- ❌ No support for arbitrary setpoint values — only the 5 validated frames above
- ❌ No model autodetect — the bit/byte map in `rs485.py` is P23B32-specific
- ❌ No formal protocol spec — this is reverse-engineering in progress on the forum

### If you contribute a new model

Please follow the same rule: only ship frames you captured and validated yourself, with the integration disabled, on your own hardware. Open a discussion on the [HA Community thread](https://community.home-assistant.io/t/joyonway-spa-control/582344) before pushing changes that affect command frames.

Thanks to [@KDy](https://community.home-assistant.io/u/kdy) for raising the CRC point publicly, and to [@Gaet78](https://community.home-assistant.io/u/gaet78) for the original P69B133 reverse-engineering work that made all of this tractable.

---

## Setpoint command lock (30s timing fix)

After a setpoint command is sent over RS485, the Joyonway controller keeps broadcasting the **old** setpoint value for about 30 seconds before it finally applies the new one. If Home Assistant reads and reacts to that "stale" broadcast during this window (typical when a climate slider mirrors `sensor.joyonway_p23b32_consigne` back into the bus), the integration ends up re-sending the old value and overwriting the user's command.

**Symptom.** You set the spa setpoint to 30 degC, then about 30 seconds later it spontaneously jumps back to 37 degC (the previous value).

**Fix.** After any setpoint command, suspend the climate-slider feedback automation for 30 seconds. Credit to [@Gaet78](https://community.home-assistant.io/u/gaet78) for documenting the underlying RS485 timing behaviour in his P69B133 integration README, which is what allowed me to identify the root cause on the P23B32.

### How it works

1. `input_boolean.spa_consigne_lock` is a visual indicator (LOCKED / FREE) you can drop on a dashboard.
2. `automation.spa_verrou_consigne_30s` triggers whenever any `script.spa_cmd_consigne_*` runs, then:
   - turns the lock ON,
   - disables the climate-slider feedback automation,
   - waits 30 seconds,
   - re-enables the climate-slider feedback,
   - turns the lock OFF.

### Install

Drop [`packages/spa_consigne_lock.yaml`](packages/spa_consigne_lock.yaml) into your `packages/` folder, then **edit the entity_id** of your own slider-to-script automation inside the file (search for the comment `Replace below`).

<details>
<summary><b>Optional dashboard tile (mini lock indicator)</b></summary>

```yaml
# 2026-05-17 | Lovelace | Mini-tile setpoint lock | Depends on: input_boolean.spa_consigne_lock
type: custom:button-card
entity: input_boolean.spa_consigne_lock
name: |
  [[[ return entity.state === 'on' ? 'LOCKED 30s' : 'FREE' ]]]
icon: |
  [[[ return entity.state === 'on' ? 'mdi:lock-clock' : 'mdi:lock-open-variant-outline' ]]]
show_state: false
styles:
  card:
    - background: "[[[ return entity.state === 'on' ? '#1a0a25' : '#08111e' ]]]"
    - border-radius: 10px
    - padding: 6px 10px
    - border: "[[[ return entity.state === 'on' ? '1px solid #b388ff66' : '1px solid #ffffff08' ]]]"
    - box-shadow: "[[[ return entity.state === 'on' ? '0 0 10px #b388ff50' : 'none' ]]]"
  name:
    - color: "[[[ return entity.state === 'on' ? '#b388ff' : '#3a3040' ]]]"
    - font-size: 9px
    - letter-spacing: 1.5px
  icon:
    - color: "[[[ return entity.state === 'on' ? '#b388ff' : '#3a3040' ]]]"
    - width: 16px
```

</details>

---

================================ FIN À COPIER ==================================


================================================================================
N'OUBLIE PAS - Mettre à jour la section ## Credits
================================================================================

Dans la section `## Credits` (ligne ~454 du README actuel), ajoute @KDy
si pas déjà présent, ou mets à jour sa ligne pour mentionner le CRC safety :

| [@KDy](https://community.home-assistant.io/u/kdy) | P25B85 controller reverse-engineering, filtration parsing reference, **CRC safety warning that shaped this repo's safety section** |


================================================================================
COMMIT MESSAGE pour GitHub Desktop
================================================================================

SUMMARY (champ titre) :
feat: setpoint lock + safety/limitations section (credits @Gaet78, @KDy)

DESCRIPTION (champ description, multi-lignes) :
Two README sections added, one package shipped.

1) Safety & Current Limitations section
   Documents the four real risks at this early stage of development:
   - CRC tolerance double-edged sword (raised by @KDy on the forum:
     a bad-CRC setpoint frame turned his heater ON unexpectedly).
   - Frame layout varies between controllers (P23B32, P25B85,
     P69B133, P20B29 all share the A1/B4 skeleton but bit positions
     and pseudo-escape scope differ).
   - Only 5 setpoint frames are currently captured and validated
     (15, 30, 37, 38, 39 degC) - the climate slider exposes 15-40
     but values outside that set have no captured frame behind them.
   - No CRC computation, no model autodetect, no formal spec.

   Includes an explicit Do/Don't table so contributors and users
   know what is safe to ship.

2) Setpoint command lock (30s)
   After a setpoint RS485 command, the controller keeps broadcasting
   the old value for about 30s. If HA's climate slider mirrors that
   stale broadcast back into a script, the user's command gets
   overwritten. Package adds:
     - input_boolean.spa_consigne_lock (visual LOCKED/FREE indicator)
     - automation.spa_verrou_consigne_30s (suspends the slider
       feedback automation for 30s after each setpoint command)
   Timing finding credited to @Gaet78 (his P69B133 README).

Bilingual yaml comments (EN/FR) in the package file. Credits section
updated for @KDy.
