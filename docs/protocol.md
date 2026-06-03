# Protocol

Canonical protocol reference for Joyonway spa RS-485 communication in this repo.
Captured from physical touchpad <-> controller bus sniffing.

This document describes the **current observed protocol** and explicitly marks
where multiple captured command-byte variants exist.

## P25B85

**Controller:** Joyonway P25B85, PCB `P2325B0003 R05`  
**Touchpad:** PB554 colour screen  
**UART:** 38400 baud, 8N1  
**Bus:** RS-485 half-duplex  

### 1. Framing

| Field | Value |
|-------|-------|
| Start delimiter | `0x1A` |
| End delimiter | `0x1D` |
| Escape byte | `0x1B` |

**Escape table** — `0x1B XX` on wire → decoded byte:

| Wire pair | Decoded byte |
|-----------|-------------|
| `1B 11` | `0x1A` |
| `1B 0B` | `0x1B` |
| `1B 13` | `0x1C` |
| `1B 14` | `0x1D` |
| `1B 15` | `0x1E` |

**Decoding order:** frame boundaries are detected on raw wire bytes FIRST
(find `0x1A` start, scan for `0x1D` end), then escape decoding is applied
to the content between delimiters.

### 2. CRC-32

All command frames carry a 4-byte CRC. Verified against 44 unique
frames across multiple capture sessions (21 session-1 + 23 phase-6).

| Parameter | Value |
|-----------|-------|
| Polynomial | `0x04C11DB7` (standard CRC-32 / Ethernet) |
| Init | `0x00000000` |
| XorOut | `0x552D22C8` |
| Reflected | No (MSB-first) |
| Message input | Unescaped bytes 0–15 (16-byte payload) |
| Preprocessing | Each 32-bit word byte-reversed before CRC |
| CRC position | Bytes 16–19 of unescaped inner frame |
| CRC byte order | Little-endian |

**Algorithm (pseudocode):**

```
function compute_crc(payload[0..15]):
    swapped = word32_swap(payload)   # reverse bytes within each 4-byte group
    crc = 0x00000000
    for each byte b in swapped:
        crc = crc XOR (b << 24)
        repeat 8 times:
            if crc bit 31 is set:
                crc = (crc << 1) XOR 0x04C11DB7
            else:
                crc = crc << 1
            crc = crc AND 0xFFFFFFFF
    return crc XOR 0x552D22C8
```

**Word swap** is due to the PB554's ARM Cortex-M MCU feeding a hardware
CRC peripheral in little-endian word order.

**Wire frame construction:**

```
inner = payload[16 bytes] + crc[4 bytes LE]
escaped = escape(inner)
wire = 0x1A + escaped + 0x1D
```

### 3. Broadcast Frames

The controller sends periodic broadcast frames (~2/sec) reporting spa state.
These are long frames (60+ bytes) prefixed with destination `0xFF`.

**Byte map** (logical frame after unescape, 0-indexed including `0x1A` start):

| Byte | Content |
|------|---------|
| 8 | Model ID (`0x03` = P25B85) |
| 9 | Water temperature (°F, raw integer) |
| 12 | Pump/jets status: `0x00`=off, `0x02`=low, `0x04`=high (manual jets ONLY — independent of automatic circulation) |
| 13 | Configuration mode flags: bit 7 (`0x80`) = Manual Ozone (clear = Auto), bit 4 (`0x10`) = Manual Heating (clear = Auto) |
| 14 | Heater/blower flags (see below) |
| 16 | Setpoint temperature (°F) |
| 17 | Light/cycle flags: bit 0 = light ON, bit 7 = heating cycle active |
| 19 | Heat slot 1 start hour + enable flag (hour \| 0x40 if enabled) |
| 21 | Heat slot 1 end hour |
| 23 | Heat slot 2 start hour + enable flag (hour \| 0x40 if enabled) |
| 25 | Heat slot 2 end hour |
| 28 | Activity flags (bit 3=blower, bit 5=activity/disinfection-related) |
| 29 | Filter slot 1 start hour + enable flag (hour \| 0x40 if enabled) |
| 31 | Filter slot 1 end hour |
| 33 | Filter slot 2 start hour + enable flag (hour \| 0x40 if enabled) |
| 35 | Filter slot 2 end hour |
| 53–58 | Date/time: year, month, day, hour, minute, second |

**Byte 14 — heater/blower states:**

| Value | Meaning |
|-------|---------|
| `0x40` | Idle (heater off, blower off) |
| `0x48` | Blower active (base `0x40` + bit 3) |
| `0x50` | Heater enabled/armed — standby (waiting for temp drop) |
| `0x51` | Circulation — pump running pre/post heat (circle icon on panel) |
| `0x54` / `0x55` | Heater actively heating (flame icon on panel) |
| `0x41` / `0xC1` | Disinfection cycle (ozone) |
| `0x58` | Blower + heater standby (base `0x50` + bit 3) |

> **Note on `0x50` ("standby"):** The controller sets byte 14 to `0x50` when
> the heater is enabled (armed). This does NOT indicate the circulation pump is
> physically running — the spa shows `0x50` for hours with 0W consumption,
> only transitioning to `0x55` when it actually heats. Byte 12 (pump) is
> independent and represents manual jets state only, not internal circulation.
> Confirmed via capture analysis: Session 1 baseline shows `0x50` + `pump=0x00`
> for the entire idle period; Phase 5 heater ON/OFF directly toggles between
> `0x40` ↔ `0x50`.
>
> **Heating cycle confirmed (session 17 capture):** Full cycle with byte 17:
> `0x40`+b17=`0x00` (off) → `0x51`+b17=`0x80` (pre-heat circ, ~2 min) →
> `0x55`+b17=`0x80` (heating, ~2 min) → `0x40`+b17=`0x80` (post-heat circ,
> ~2 min, circle icon) → `0x40`+b17=`0x00` (off). Byte 17 bit 7 (`0x80`)
> is the **heating cycle active** flag — set for the entire cycle including
> post-heat circulation. Byte 28 bit 5 (`0x20`) also tracks cycle state
> with identical transitions.
>
> **Byte 12 / byte 14 independence confirmed (guided capture 2026-06-02):**
> Byte 12 (pump/jets) exclusively reflects manual jets state and is completely
> independent of byte 14 (heater/circulation state). During active circulation
> (`h=0x51`), pump byte stays `0x00` unless the user manually activates jets.
> When jets are manually set to low (`p=0x02`) or high (`p=0x04`), they remain
> at that value through all heating/circulation transitions (standby → heating →
> post-heat circ). Turning jets off during active circulation correctly sets
> `p=0x00` even though the circulation pump continues running internally.
> The spa's display shows jets and circulation as two independent icons,
> matching this byte-level independence.

**Byte 14 bit fields:**
- Bit 0: disinfection active (`0x01`)
- Bit 3: blower active (`0x08`)
- Bit 4: circulation/heating base (`0x10`)
- Bit 6: base idle (`0x40`)
- Bit 7: disinfection variant flag (`0x80`, seen in `0xC1`)

For disinfection state, byte 14 (`0x41`/`0xC1`) is the authoritative indicator.
Byte 28 bit 5 is useful activity context but not UV-specific on its own.

### 4. Command Frames

All commands are 22 bytes on wire (may be 23 if CRC contains an escaped
byte). Unescaped inner frame is always 20 bytes: 16 payload + 4 CRC.

**Command type** is determined by byte 4 of the unescaped inner frame:

| Byte 4 | Type | Description |
|--------|------|-------------|
| `0xA1` | Button press | Light, pump, heater, blower, temperature |
| `0xA2` | DateTime set | Set the spa's internal clock |
| `0xA3` | Heat schedule | Program heating time windows |
| `0xA4` | Filter schedule | Program filtration time windows |

**Common header** (bytes 0–6, same across all command types):

```
01 20 10 3C [type] 10 A1
```

Where `[type]` = `A1` / `A2` / `A3` / `A4`.

### 4.1. Button Commands (type 0xA1)

**Payload layout** (16 bytes, 0-indexed):

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A1 10 A1` |
| 7 | Pump command byte 1 (transition encoding) |
| 8 | Pump command byte 2 (transition encoding) |
| 9 | Button group identifier |
| 10 | Button action / value |
| 11 | Modifier byte (usually `0x00`; `0x80` for ozone mode) |
| 12 | Context byte (usually `0xC0`; `0x40` for ozone manual mode) |
| 13 | Always `0x00` |
| 14 | Current setpoint (°F) at time of command |
| 15 | Always `0x00` |

**Button group byte (byte 9) and action byte (byte 10):**

| Button | byte[9] | byte[10] ON | byte[10] OFF | Notes |
|--------|---------|-------------|--------------|-------|
| Light | `0x40` | `0x40` | same (toggle) | Same frame for ON/OFF |
| Heater | `0x08` | `0x18` / `0x08` | `0x11` / `0x00` | Two session variants observed |
| Blower | `0x04` | `0x0C` | `0x00` | Distinct ON/OFF (**live confirmed**) |
| Temperature | `0x80` | `0x98` | — | **Live confirmed**; `0x80` does NOT work |
| Ozone manual | `0x01` | `0x01` | `0x10` | Distinct ON/OFF; ozone mode must be Manual |

**Ozone mode commands** (byte[9]=`0x00`, byte[11]=`0x80`):

| Mode | byte[12] | Notes |
|------|----------|-------|
| Auto | `0xC0` | Standard scheduling mode |
| Manual | `0x40` | Enables manual ON/OFF from panel/RS485 |

These use the pattern: `pump_b9=0x00, btn=0x00, modifier=0x80` with byte[12]
distinguishing the mode. The mode setting is reflected in the broadcast frame
at byte 13 bit 7 (`0x80` = Manual, clear = Auto).

**Heater mode commands** (byte[9]=`0x00`, byte[11]=`0x40`):

| Mode | byte[12] | Notes |
|------|----------|-------|
| Auto | `0x80` | Standard scheduling/automatic heating mode |
| Manual | `0xC0` | Enables manual ON/OFF heating toggle |

These use the pattern: `pump_b9=0x00, btn=0x00, modifier=0x40` with byte[12]
distinguishing the mode. The mode setting is reflected in the broadcast frame
at byte 13 bit 4 (`0x10` = Manual, clear = Auto).

**Pump commands** use bytes 7–8 (panel usually emits transition patterns):

| Transition | byte[7] | byte[8] | byte[9] | byte[10] |
|------------|---------|---------|---------|----------|
| OFF → Low | `0x02` | `0x02` | `0x00` | `0x00` |
| Low → High | `0x06` | `0x04` | `0x00` | `0x00` |
| High → OFF | `0x04` | `0x00` | `0x00` | `0x00` |

> **Note on legacy pump commands:** Earlier sessions captured byte[10]=`0x08`
> for pump transitions. Phase 6 captures show `0x00`. Both appear to work.
> The controller likely ignores byte[10] for pump commands.

**Controller-accepted jets transition bytes (state-dependent hardware behavior):**

The controller does not accept arbitrary direct writes to target states regardless of the current state. Instead, it enforces state-dependent transition rules based on the following three transition commands:

| Transition Command | byte[7] | byte[8] | Controller Behavior by Current State |
|--------------------|---------|---------|--------------------------------------|
| **OFF → LOW**      | `0x02`  | `0x02`  | • From **OFF**: transitions to **LOW**.<br>• From **HIGH**: transitions to **OFF** (aborted/invalid transition fallback).<br>• From **LOW**: ignored. |
| **LOW → HIGH**     | `0x06`  | `0x04`  | • From **LOW**: transitions to **HIGH**.<br>• From **OFF**: transitions directly to **HIGH**.<br>• From **HIGH**: ignored. |
| **HIGH → OFF**     | `0x04`  | `0x00`  | • From **HIGH**: transitions to **OFF**.<br>• From **LOW**: ignored.<br>• From **OFF**: ignored. |

Because the controller ignores commands that do not correspond to permitted transitions:
- **LOW → OFF** cannot be achieved directly. The integration must transition **LOW → HIGH** (`0x06, 0x04`), wait for the state to update to HIGH, and then transition **HIGH → OFF** (`0x04, 0x00`).
- **HIGH → LOW** cannot be achieved directly. The integration must transition **HIGH → OFF** (`0x04, 0x00`), wait for the state to update to OFF, and then transition **OFF → LOW** (`0x02, 0x02`). Sending `(0x02, 0x02)` while HIGH will cause a hard shutdown to OFF.

This matches the physical touch panel UI behavior (which cycles OFF → LOW → HIGH → OFF) and the observed RS-485 behavior in sniffer captures.

### 4.2. DateTime Set (type 0xA2)

Sets the spa's internal clock.

**Payload layout:**

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A2 10 A1` |
| 7 | Prefix byte (see below) |
| 8 | Year (offset from 2000, e.g. `0x1A` = 26 = 2026) |
| 9 | Month |
| 10 | Day |
| 11 | Hour (24h) |
| 12 | Minute |
| 13 | Second |
| 14 | `0x00` |
| 15 | `0x00` |

**Prefix byte (byte 7) — controls what is written:**

| Value | Effect |
|-------|--------|
| `0x05` | Write **date + time** (Y/M/D + H:M:S) |
| `0x50` | Write **time only** (H:M:S; date unchanged) |

Captured from PB554 panel (date change sessions):
- `05 19 04 1A 17 0A 00 00 00` → set 2025-04-26 23:10:00 (date+time) ✅
- `05 18 03 19 16 0B 00 00 00` → set 2024-03-25 22:11:00 (date+time) ✅
- `50 19 04 1A 16 0B 00 00 00` → set time 22:11:00 only (date unchanged) ✅

Previously captured (time-only writes):
- `50 1A 05 15 16 35 00 00 00` → 2026-05-21 22:53:00 (time only)
- `50 1A 05 15 0F 09 00 00 00` → 2026-05-21 15:09:00 (time only)

### 4.3. Heat Schedule (type 0xA3)

Programs heating time windows.

**Payload layout:**

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A3 10 A1` |
| 7 | Enable flags byte (see below) |
| 8 | Slot 1 start hour |
| 9 | Slot 1 start minute |
| 10 | Slot 1 end hour |
| 11 | Slot 1 end minute |
| 12 | Slot 2 start hour |
| 13 | Slot 2 start minute |
| 14 | Slot 2 end hour |
| 15 | Slot 2 end minute |

**Flags byte (byte 7) — state encoding (enable/disable intent):**

| Value | Slot 1 | Slot 2 | Binary |
|-------|--------|--------|--------|
| `0xAA` | ✅ Enabled | ✅ Enabled | `10101010` |
| `0x62` | ✅ Enabled | ❌ Disabled | `01100010` |
| `0x9A` | ❌ Disabled | ✅ Enabled | `10011010` |
| `0x52` | ❌ Disabled | ❌ Disabled | `01010010` |

The encoding uses 2-bit pairs per slot (not single-bit flags). The same values
apply to both heat and filter schedules — the command type byte (`0xA3` vs
`0xA4`) distinguishes them.

> **Verified (Phase 6):** Three of the four values captured live (`0xAA`,
> `0x62`, `0x9A`). The fourth (`0x52`) is derived by XOR consistency and
> verified by CRC match. `test_build_schedule_command_phase6_match` confirms
> byte-for-byte frame identity against captured wire data.

**Slot 2 write quirk — time-write flags:**

For schedule **time edits**, the controller may ignore slot 2 time values when
slot 2 is disabled unless a force-write variant is used. PB554 panel captures
show distinct flags for time-write intent versus pure enable-state intent.

The PB554 panel works around this by sending a different flags byte when the
user edits times on disabled slots. Captures from 2026-05-31 confirm:

| Scenario on panel | Flags byte | Meaning |
|-------------------|-----------|---------|
| Only slot 1 time edited (both disabled) | `0x52` | Normal both-off |
| Only slot 2 time edited (both disabled) | `0x58` | Force-write slot 2 times |
| Both slot 1 AND slot 2 times edited (both disabled) | `0x5A` | Force-write both slots |
| Slot 1 enabled, slot 2 disabled, edit slot 2 time | `0x6A` | Force-write slot 2 while s1 remains enabled |

Binary analysis:
- `0x52` = `01010010` — base "both disabled" state flags
- `0x58` = `01011000` — slot2-write variant (both disabled)
- `0x5A` = `01011010` — force-write both (both disabled)
- `0x6A` = `01101010` — slot2-write variant when s1=on/s2=off

**Implementation (confirmed):** treat schedule commands with two intent modes:

1. **State mode** (slot enable/disable commands):
   - `(on, on)=0xAA`, `(on, off)=0x62`, `(off, on)=0x9A`, `(off, off)=0x52`
2. **Time-write mode** (schedule time edits):
   - `(on, on)=0xAA`, `(on, off)=0x6A`, `(off, on)=0x9A`, `(off, off)=0x5A`

This mirrors panel behavior and keeps slot2 time writes reliable while disabled.

> **Verified (2026-05-31 captures):** Flags bytes `0x52`, `0x58`, `0x5A`, and
> `0x6A` captured live from PB554 panel across heat/filter scenarios.
> Each capture contains exactly 1 schedule command per change, confirming the
> panel sends a single frame regardless of how many slots are edited.
>
> **Verified (2026-05-31 write tests):** `0x5A` confirmed working for all
> cases: changing only slot 1 (slot 2 unchanged), changing only slot 2
> (slot 1 unchanged), and changing both slots simultaneously. 8/8 automated
> tests passed with user panel confirmation. Safe to use universally.

### 4.4. Filter Schedule (type 0xA4)

Programs filtration time windows.

**Payload layout:** Same as heat schedule (section 4.3) but with type `0xA4`.

**Flags byte (byte 7) — slot enable encoding:**

Same lookup table as heat schedule (section 4.3): `0xAA` = both on,
`0x62` = s1 on / s2 off, `0x9A` = s1 off / s2 on, `0x52` = both off.

> **Verified (Phase 6):** Filter slot enable/disable confirmed to use the same
> flags byte encoding as heat schedules.

### 5. Captured Frame Examples

All frames below are complete wire-level hex (including `0x1A` start and
`0x1D` end delimiters). Escape sequences are present where needed. CRC has
been verified for all frames.

#### 5.1. Button Commands (Phase 6 capture — 2026-05-27)

| Action | Wire hex |
|--------|----------|
| Light toggle | `1a0120103ca110a10000404000c0006200bcdb13931d` |
| Pump OFF → Low | `1a0120103ca110a10202000000c0006200f138e94a1d` |
| Pump Low → High | `1a0120103ca110a10604000000c000620070f8dce71d` |
| Pump High → OFF | `1a0120103ca110a10400000000c0006200ffbdc5c81d` |
| Blower ON | `1a0120103ca110a10000040400c0006200f4b922821d` |
| Blower OFF | `1a0120103ca110a10000040000c00062000704bebb1d` |
| Heater ON (variant 1) | `1a0120103ca110a10000081800c00062000dd6159b1d` |
| Heater OFF (variant 1) | `1a0120103ca110a10000081100c0006200fac57ba71d` |
| Temperature +2°F (98→100) | `1a0120103ca110a10000808000c0006400430ed65b1d` |
| Temperature −2°F (100→98) | `1a0120103ca110a10000809900c00062006a0119851d` |
| Ozone mode → Auto | `1a0120103ca110a10000000080c0006200d4641b15ae1d` |
| Ozone mode → Manual | `1a0120103ca110a10000000080400062003a8412c71d` |
| Ozone manual ON | `1a0120103ca110a100000101004000620060b46dea1d` |
| Ozone manual OFF | `1a0120103ca110a1000001100040006200bd2b48431d` |
| Heater mode → Auto | `1a0120103ca110a1000000004080006200d2e5785d1d` |
| Heater mode → Manual | `1a0120103ca110a10000000040c0006200a595fe691d` |

#### 5.2. Configuration Commands (Phase 6 capture)

| Action | Wire hex |
|--------|----------|
| DateTime set (→09:12:00) | `1a0120103ca210a1501b11051b0b090c0000001b0b16f6891d` |
| Heat sched (s1 off, s2 on) | `1a0120103ca310a19a0c001000150016009eab3f581d` |
| Heat sched (both on) | `1a0120103ca310a1aa0c001000150016003efb8dd91d` |
| Heat sched (s1 on, s2 off) | `1a0120103ca310a1620c00100015001600e09a71e91d` |
| Filter sched (s1 on, s2 off)| `1a0120103ca410a1620b000d0011001200a040f5321d` |
| Filter sched (both on) | `1a0120103ca410a1aa0b000d00110012007e2109021d` |

#### 5.3. Button Commands (earlier sessions — pre-Phase 6)

These were captured in earlier sessions. CRC verified. Byte[10] values differ
from Phase 6 for some commands (session-dependent), but all are accepted by
the controller.

| Action | Wire hex |
|--------|----------|
| Light toggle | `1a0120103ca110a10000404000c00056003031eeb21d` |
| Pump OFF → Low | `1a0120103ca110a10202000000c00056007dd2146b1d` |
| Pump Low → High | `1a0120103ca110a10604000000c0005600fc1221c61d` |
| Pump High → OFF | `1a0120103ca110a10400000000c0005600735738e91d` |
| Heater ON | `1a0120103ca110a10000080800c0006400d3cab4791d` |
| Heater OFF | `1a0120103ca110a10000080000c000640035b18d0a1d` |
| Blower ON | `1a0120103ca110a10000040c00c00064000029c8f51d` |
| Blower OFF | `1a0120103ca110a10000040800c0006400f39454cc1d` |
| DateTime set | `1a0120103ca210a1501b1105150f090000004cbc3d971d` |
| Filter schedule | `1a0120103ca410a1aa0d000c0011001200f605b0ff1d` |
| Heat schedule | `1a0120103ca310a1620e001000140016004d48aa7f1d` |

#### 5.4. Same-session button bytes comparison

| Action | Session 1 byte[10] | Phase 6 byte[10] | Notes |
|--------|--------------------|-------------------|-------|
| Light toggle | `0x58` | `0x40` | Both toggle, both work |
| Heater ON | `0x08` | `0x18` | Distinct variant |
| Heater OFF | `0x00` | `0x11` | Distinct variant |
| Blower ON | `0x04` | `0x04` | Same |
| Blower OFF | `0x00` | `0x00` | Same |
| Pump transitions | `0x08` | `0x00` | Controller likely ignores |

### 6. Temperature Command Byte Encoding

Temperature commands (type `0xA1`, byte[9]=`0x80`) encode the **target**
setpoint in byte 14 as the Fahrenheit value directly.

**Byte 10:** Use `0x98` — **confirmed working via live test**. `0x80` was
tested and does NOT work. Other variants (`0x99`) were observed in older
captures but `0x98` is the reliable value used by the integration.

**Example:** to set thermostat to 95°F (35°C), build payload:

```
01 20 10 3C A1 10 A1 00 00 80 [variant] 00 C0 00 5F 00
```

Then call `compute_crc(payload)` and `build_frame(payload)`.


### 6b. Schedule Broadcast Encoding

The controller broadcasts schedule state in bytes 19–35 of the broadcast frame.
Each schedule has 2 time slots, encoded as 4 bytes per slot pair:

```
[slot1_start] 00 [slot1_end] 00 [slot2_start] 00 [slot2_end]
```

**Enable flag:** The start byte of each slot uses bit 6 (0x40) as a slot-enabled
flag. The actual hour is in the lower 6 bits (mask 0x3F).

| Raw byte | Enabled? | Hour |
|----------|----------|------|
| `0x4B` | Yes (bit 6 set) | 0x0B = 11 |
| `0x14` | No (bit 6 clear) | 0x14 = 20 |
| `0x51` | Yes (bit 6 set) | 0x11 = 17 |

**Byte positions:**

| Bytes | Schedule |
|-------|----------|
| 19, 21, 23, 25 | Heat: slot1 start, slot1 end, slot2 start, slot2 end |
| 29, 31, 33, 35 | Filter: slot1 start, slot1 end, slot2 start, slot2 end |

**Example** (live capture):
- Byte 19 = `0x4B` → heat slot 1: start=11:00, enabled
- Byte 21 = `0x10` → heat slot 1: end=16:00
- Byte 23 = `0x14` → heat slot 2: start=20:00, disabled
- Byte 25 = `0x16` → heat slot 2: end=22:00
- Byte 29 = `0x4B` → filter slot 1: start=11:00, enabled
- Byte 31 = `0x0C` → filter slot 1: end=12:00
- Byte 33 = `0x51` → filter slot 2: start=17:00, enabled
- Byte 35 = `0x12` → filter slot 2: end=18:00


### 7. Notes

- **Light is a toggle** — same frame for ON and OFF. Software must track
  state and refuse to send when state is unknown.
- **Pump commands are state-dependent** — physical panel UI is a cycle (OFF → LOW → HIGH → OFF), and the controller's RS-485 transition commands reflect this. Direct commands for LOW → OFF and HIGH → LOW do not exist/fail (e.g. sending LOW command when HIGH triggers a shutdown to OFF). The integration must execute sequenced transitions (LOW → HIGH → OFF and HIGH → OFF → LOW) by waiting for state feedback.
- **Heater and blower** have distinct ON/OFF frames — safe to send
  regardless of current state.
- **Ozone / disinfection** can be toggled via RS485 when mode is set to
  Manual. Send the ozone manual ON/OFF command directly. Auto mode is
  schedule-only. Mode (Auto↔Manual) is set separately via the ozone mode
  command. Broadcast byte 14 transitions: `0x40` → `0xC1` (ozone on),
  `0xC1` → `0x40` (ozone off). Mode setting is broadcast at byte 13 bit 7
  (`0x80` = Manual, clear = Auto) — confirmed from phase 6 captures.
- **Setpoint byte (byte 14)** is the CURRENT setpoint at time of capture,
  embedded in every button command. For non-temperature commands, it acts
  as a "current state echo." The controller accepts commands regardless of
  this byte's value matching actual state.
- **Auto-off**: pump high speed auto-stops after 20 minutes (hardware timer).
- **Panel-local settings** confirmed: Auto Lock, Brightness, Screen Flip, and the **About / Diagnostics screens** (Panel ID, Panel Version, Board Version, capability displays like Jets 1 Two Speed, Blower Yes, Ozone Yes, Cycle Pump No) produce no RS485 command frames and no broadcast state changes. Sniff/differential capture analysis verified that during menu click-through, 100% of the broadcast payload remains bit-identical (outside of the ticking clock seconds field) and no interactive query/response traffic occurs. These parameters are stored and handled entirely locally by the display panel.
- **Light color mode**: Physical spa testing and manual review verified that the spa hardware does not support color selection commands. The light automatically cycles through colors locally, and the controller only supports standard ON/OFF toggling via RS485.
- **Byte 17 bit 7** (`0x80`): Set during heating and disinfection. Not a
  light flag — appears to be a general "active operation" indicator.
  The actual light state is byte 17 bit 0 only.

### 8. CRC Derivation Notes

This section summarizes how the CRC parameters were derived and why the final
model is trustworthy, without repeating the full brute-force notebook history.

**Evidence set**

- 21 unique same-session command frames were used, covering every known command
  family: temperature, light, pump, heater, blower, datetime, filter schedule,
  and heat schedule.
- Delta/linearity checks behaved exactly like a CRC-family transform, which
  justified polynomial-focused search methods.

**How the polynomial was found**

- Exhaustive search over all 2^32 CRC-32 polynomials produced one valid result:
  `0x04C11DB7`.
- The key discovery was that payload bytes are processed after **32-bit word
  byte-swap**. Before accounting for this transform, GCD/constraint attempts
  failed because byte-position assumptions were wrong.

**Why `xor_out = 0x552D22C8` is expected**

- This is not an anomaly; it is an equivalent CRC parameterization for this
  message shape.
- Because command headers are mostly constant, their contribution is absorbed
  into an effective output constant, represented here explicitly as `xor_out`
  to keep generation deterministic and reproducible.

**Final canonical model (used in this repo)**

- Polynomial: `0x04C11DB7` (non-reflected, MSB-first)
- Init: `0x00000000`
- Preprocessing: word32 byte-swap on payload bytes `0..15`
- CRC storage: little-endian at bytes `16..19`
- Verification: full match on all 21 unique same-session frames
- Phase 6 verification: 23 additional unique frames, 100% match

