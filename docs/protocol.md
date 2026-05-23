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

All command frames carry a 4-byte CRC. Verified against 21 unique
same-session frames covering all command types.

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
| 12 | Pump status: `0x00`=off, `0x02`=low, `0x04`=high |
| 14 | Heater/blower flags (see below) |
| 16 | Setpoint temperature (°F) |
| 17 | Light flags (bit 0 = light ON) |
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
| `0x50` | Circulation pump running |
| `0x54` / `0x55` | Heater active |
| `0x41` / `0xC1` | Disinfection cycle |
| `0x58` | Blower active (base `0x50` + bit 3) |

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
| 7 | Pump byte high (transition target) |
| 8 | Pump byte low (transition source) |
| 9 | Button group identifier |
| 10 | Button value (ON/OFF) |
| 11 | Always `0x00` |
| 12 | Always `0xC0` |
| 13 | Always `0x00` |
| 14 | Current setpoint (°F) at time of command |
| 15 | Always `0x00` |

**Button group byte (byte 9) and value byte (byte 10):**

| Button | byte[9] | byte[10] ON | byte[10] OFF |
|--------|---------|-------------|--------------|
| Light | `0x40` | `0x58` (same-session) / `0x40` (legacy replay) | same as ON (toggle) |
| Heater | `0x08` | `0x08` | `0x00` |
| Blower | `0x04` | `0x04` (same-session) / `0x0C` (legacy replay) | `0x00` (same-session) / `0x08` (legacy replay) |
| Temperature | `0x80` | `0x80`/`0x99`/`0x98` | — |

Current integration is-state: entity writes still use replay/lookup frames from
`custom_components/joyonway_p25b85/adapters/p25b85.py` (legacy variants for
light/blower, same CRC-valid frame family).

**Pump commands** use bytes 7–8 (pump state transition):

| Transition | byte[7] | byte[8] | byte[9] | byte[10] |
|------------|---------|---------|---------|----------|
| OFF → Low | `0x02` | `0x02` | `0x00` | `0x08` |
| Low → High | `0x06` | `0x04` | `0x00` | `0x08` |
| High → OFF | `0x04` | `0x00` | `0x00` | `0x08` |

### 4.2. DateTime Set (type 0xA2)

Sets the spa's internal clock.

**Payload layout:**

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A2 10 A1` |
| 7 | `0x50` (fixed prefix) |
| 8 | Year (offset from 2000, e.g. `0x1A` = 26 = 2026) |
| 9 | Month |
| 10 | Day |
| 11 | Hour (24h) |
| 12 | Minute |
| 13 | Second |
| 14 | `0x00` |
| 15 | `0x00` |

Verified from two captures at known times:
- `50 1A 05 15 16 35 00 00 00` → 2026-05-21 22:53:00
- `50 1A 05 15 0F 09 00 00 00` → 2026-05-21 15:09:00

### 4.3. Heat Schedule (type 0xA3)

Programs heating time windows.

**Payload layout:**

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A3 10 A1` |
| 7 | Config/flags byte |
| 8 | Duration or slot ID |
| 9 | `0x00` |
| 10 | Slot 1 start hour |
| 11 | `0x00` |
| 12 | Slot 1 end hour |
| 13 | `0x00` |
| 14 | Slot 2 start hour |
| 15 | `0x00` (padding or slot 2 end) |

> Broadcast byte[19] changes when a heat schedule is written.

### 4.4. Filter Schedule (type 0xA4)

Programs filtration time windows.

**Payload layout:**

| Bytes | Content |
|-------|---------|
| 0–6 | Header: `01 20 10 3C A4 10 A1` |
| 7 | Config/flags byte |
| 8 | Duration or slot ID |
| 9 | `0x00` |
| 10 | Slot 1 start hour |
| 11 | `0x00` |
| 12 | Slot 1 end hour |
| 13 | `0x00` |
| 14 | Slot 2 start hour |
| 15 | `0x00` (padding or slot 2 end) |

> Broadcast byte[29] changes (`0x4C` → `0xCD`) when a filter schedule is written.

### 5. Captured Frame Examples

All frames below are complete wire-level hex (including `0x1A` start and
`0x1D` end delimiters). Escape sequences are present where needed. CRC has
been verified for all same-session frames.

#### 5.1. Button Commands (same-session capture)

| Action | Wire hex |
|--------|----------|
| Temperature +1 (setpoint 102°F) | `1a0120103ca110a10000808000c00066004d5767581d` |
| Temperature +1 (setpoint 104°F) | `1a0120103ca110a10000809900c00068005c3c6d881d` |
| Temperature −1 (setpoint 98°F) | `1a0120103ca110a10000809900c00062006a0119851d` |
| Temperature −1 (setpoint 96°F) | `1a0120103ca110a10000809900c00060006458a8861d` |
| Temperature −1 (setpoint 95°F) | `1a0120103ca110a10000809800c0005f00484baee41d` |
| Temperature −1 (setpoint 93°F) | `1a0120103ca110a10000809800c0005d0046121fe71d` |
| Temperature +1 (setpoint 78°F) | `1a0120103ca110a10000808000c0004e0095a3b76d1d` |
| Temperature +1 (setpoint 80°F) | `1a0120103ca110a10000808000c0005000cfe42b7a1d` |
| Temperature −1 (setpoint 77°F) | `1a0120103ca110a10000808000c0004d001b1356de6f1d` |
| Light ON (toggle) | `1a0120103ca110a10000405800c0005d00ab2c092b1d` |
| Light OFF (toggle) | `1a0120103ca110a10000405800c0005d00ab2c092b1d` |
| Pump OFF → Low | `1a0120103ca110a10202000800c0005d002a3881141d` |
| Pump Low → High | `1a0120103ca110a10604000800c0005d00abf8b4b91d` |
| Pump High → OFF | `1a0120103ca110a10400000800c0005d0024bdad961d` |
| Heater ON | `1a0120103ca110a10000080800c0005d00fc5a36501d` |
| Heater OFF | `1a0120103ca110a10000080000c0005d001b11210f231d` |
| Blower ON | `1a0120103ca110a10000040400c0005d00c9c273af1d` |
| Blower OFF | `1a0120103ca110a10000040000c0005d003a7fef961d` |

#### 5.2. Configuration Commands (same-session capture)

| Action | Wire hex |
|--------|----------|
| DateTime set | `1a0120103ca210a1501b110515163500000087ecf6541d` |
| Filter schedule | `1a0120103ca410a1aa0c000c00110012007b62bdb61d` |
| Heat schedule | `1a0120103ca310a1620c001000140016005787b0ed1d` |

#### 5.2.1. Same-session button bytes (double-check from captures)

Extracted from `tools/captures_crc/crc_session.json` payload bytes 9-10:

| Action | byte[9] | byte[10] |
|--------|---------|----------|
| Light toggle | `0x40` | `0x58` |
| Heater ON / OFF | `0x08` | `0x08` / `0x00` |
| Blower ON / OFF | `0x04` | `0x04` / `0x00` |
| Pump transitions | `0x00` | `0x08` |

#### 5.3. Button Commands (earlier sessions — pre-CRC, replay-only)

These were captured in earlier sessions. CRC is baked into the frame but
was not independently verifiable at the time. Use as replay frames or for
reference; the CRC formula can regenerate them if payloads are known.

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

### 6. Temperature Command Byte Encoding

Temperature commands (type `0xA1`, byte[9]=`0x80`) encode the **target**
setpoint in byte 14 as the Fahrenheit value directly.

**Byte 10 variants** observed across capture sessions:

| Byte 10 | Notes |
|---------|-------|
| `0x80` | Observed at lower temperatures (< 80°F) |
| `0x98` | Observed at mid-range temperatures |
| `0x99` | Observed at higher temperatures (> 100°F) |

Byte 10 appears session-dependent (possibly based on current pump/heater
state at time of capture). With CRC cracked, any variant can be tested.

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
- **Pump is a cycle** — OFF → Low → High → OFF. Panel always sends the
  specific transition frame based on current state; there is no "set to X"
  command.
- **Heater and blower** have distinct ON/OFF frames — safe to send
  regardless of current state.
- **Setpoint byte (byte 14)** is the CURRENT setpoint at time of capture,
  embedded in every button command. For non-temperature commands, it acts
  as a "current state echo." The controller accepts commands regardless of
  this byte's value matching actual state.
- **Auto-off**: pump high speed auto-stops after 20 minutes (hardware timer).
- **Disinfection cycle** (ozone) is schedule-only; cannot be toggled or
  cancelled via RS-485 from the PB554.

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

