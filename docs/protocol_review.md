# Protocol.md Review: Cross-Reference Analysis

Review of the restructured [protocol.md](file:///Users/alex/repositories/alexbde/ha-joyonway/docs/protocol.md) against the main branch version and the [P23 repo](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo).

## Errors Found

### 🔴 1. P25 Setpoint Command: Wrong Variant Byte (Line 134)

> [!CAUTION]
> The P25 setpoint command uses `0x80` as the btn_action byte, but the code and main branch protocol.md confirm that **`0x80` does NOT work** on the P25. The correct value is **`0x98`**.

| | Current protocol.md | Code ([p25b85.py:510](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L510)) | Main branch doc |
|---|---|---|---|
| Setpoint payload | `... 80 80 00 C0 00 [temp_f] 00` | `btn_action=0x98` → `... 80 98 00 C0 00 [temp_f] 00` | `0x98` confirmed, `0x80` tested and does NOT work |

**Fix:** Change `80 80` to `80 98` in the P25 setpoint row (byte 10 from `0x80` → `0x98`). The P23 setpoint correctly uses `80 80 02 04`.

### 🔴 2. P23 Pump/Blower Commands: Wrong Payload Size (Lines 131-133)

> [!CAUTION]
> The P23 pump and blower commands are documented as **14-byte** payloads, but the actual P23 repo code ([rs485.py](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L36-L47)) shows they are **16-byte** payloads with the standard `00 A1` prefix at bytes 5-6.

**Actual unescaped payloads from P23 code:**

| Command | Documented (14-byte) | Actual (16-byte) |
|---|---|---|
| Left Pump ON | `01 30 10 3C A1 06 04 00 00 02 04 00 00 00` | `01 30 10 3C A1 00 A1 06 04 00 00 02 04 00 00 00` |
| Left Pump OFF | `01 30 10 3C A1 06 00 00 00 02 04 00 00 00` | `01 30 10 3C A1 00 A1 06 00 00 00 02 04 00 00 00` |
| Right Pump ON | `01 30 10 3C A1 18 10 00 00 02 04 00 00 00` | `01 30 10 3C A1 00 A1 18 10 00 00 02 04 00 00 00` |
| Right Pump OFF | `01 30 10 3C A1 18 00 00 00 02 04 00 00 00` | `01 30 10 3C A1 00 A1 18 00 00 00 02 04 00 00 00` |
| Blower ON | `01 30 10 3C A1 00 00 04 04 02 04 00 00 00` | `01 30 10 3C A1 00 A1 00 00 04 04 02 04 00 00 00` |
| Blower OFF | `01 30 10 3C A1 00 00 04 00 02 04 00 00 00` | `01 30 10 3C A1 00 A1 00 00 04 00 02 04 00 00 00` |

All pump/blower payloads are **16 bytes** with `00 A1` at positions 5-6, consistent with how the P23 command prefix is `01 30 10 3C [Type] 00 A1` (7-byte header). The "14-byte" label is incorrect.

### 🔴 3. P25 Blower Command: Wrong btn_action Bytes (Line 133)

> [!WARNING]
> The P25 blower ON command uses `04 04` in protocol.md but the main branch wire captures show `04 0C` and the code ([p25b85.py:494](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L492-L495)) uses `btn_action=0x0C`.

| | Protocol.md | Wire capture (main branch) | Code |
|---|---|---|---|
| Blower ON | `04 04 00 C0` | `04 0C 00 C0` | `btn_action=0x0C` |
| Blower OFF | `04 00 00 C0` | `04 08 00 C0` | `btn_action=0x00` |

The main branch protocol.md Section 5.4 explains the variant difference: Session 1 byte[10] was `0x04`/`0x00` (btn_group only), Phase 6 byte[10] was `0x04`/`0x00` (same). But the actual wire hex in Section 5.3 shows `0x0C` and `0x08` for the full btn_action field. The current protocol.md picked the session-1 shorthand values rather than the full payload values.

**Fix:** Use the code values — **ON: `04 0C`, OFF: `04 00`**. The wire capture OFF frame shows `0x08` but that's likely a session artifact (blower was already running, ORing 0x08 into the byte echo); the code uses `0x00` for OFF.

### 🟡 4. P23 Unescape Boundary: Wrong Index (Line 40)

> [!WARNING]
> Protocol.md says "Only tail bytes (indices 53 and higher) should be unescaped. Unescaping indices 0–52 can corrupt payload parsing."

The actual code in [protocol.py:107-109](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/protocol.py#L107-L109) uses `frame[:55]` (keeping bytes 0-54 raw, unescaping from index 55+).

**Fix:** Change "indices 53 and higher" → "indices 55 and higher" and "Unescaping indices 0–52" → "Unescaping indices 0–54".

## P25 Information Missing (from main branch)

### 🟡 5. Missing Behavioral Notes

The main branch protocol.md Section 7 "Notes" contained several important P25-specific behavioral observations not carried over:

| Topic | Main branch info | In new doc? |
|---|---|---|
| **Pump auto-off** | Pump high speed auto-stops after 20 minutes (hardware timer) | ❌ Missing |
| **Panel-local settings** | Auto Lock, Brightness, Screen Flip, About/Diagnostics screens produce no RS485 traffic | ❌ Missing |
| **Light color mode** | Spa hardware does not support color commands; light auto-cycles colors locally | ❌ Missing |
| **Setpoint byte echo** | Byte 14 of every command is a current setpoint echo; controller ignores it | ❌ Missing |
| **Pump state-dependent** | Pump commands are state-dependent cycle transitions; direct LOW→OFF / HIGH→LOW fail | Partially (noted in table but no detail) |
| **Light is a toggle** | P25 light is toggle-only (same frame for ON and OFF) | Partially (noted in table) |

### 🟡 6. P25 Heater Command Variant Ambiguity (Line 135)

The protocol.md uses the Phase 6 variant bytes for heater commands (`0x18`/`0x11`) but the code uses Session 1 variant (`0x08`/`0x00`). Both work on the P25 controller per the main branch documentation. This isn't wrong, but it's inconsistent with the running code. Consider noting both variants.

### 🟡 7. P25 Blower Source Byte Clarification (Line 98)

Protocol.md says P25 blower is at "Byte 14, bit `0x08`" with a parenthetical "(P25 also mirrors at Byte 28 bit 3)". In the actual P25 code, blower state is read from **Byte 28** ([p25b85.py:303](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L303)). Byte 14 bit 0x08 is used as `MASK_HEATER_BLOWER` and is stripped before the heater state lookup.

While both bytes contain blower info, the primary source in code is byte 28. Consider swapping the description to: "Byte 28, bit `0x08` (also reflected in Byte 14 bit 3)".

## P23/P20 Information Verification

### ✅ Correct P23/P20 Details

| Item | Protocol.md | P23 Repo Source | Verdict |
|---|---|---|---|
| Model ID `0x02` | ✓ | [rs485.py:68](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L68) `BROADCAST_SIGNATURE` byte 8 = `0x02` | ✅ |
| Water temp byte 9 | ✓ | [rs485.py:70](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L70) `IDX_WATER_TEMP = 9` | ✅ |
| Setpoint byte 16 | ✓ | [rs485.py:73](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L73) `IDX_SETPOINT = 16` | ✅ |
| Light byte 17 bit 0x01 | ✓ | [rs485.py:82](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L82) `MASK_LUMIERE = 0x01` | ✅ |
| Left pump byte 12 bit 0x04 | ✓ | [rs485.py:75](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L75) `MASK_POMPE_GAUCHE = 0x04` | ✅ |
| Right pump byte 12 bit 0x10 | ✓ | [rs485.py:76](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L76) `MASK_POMPE_DROITE = 0x10` | ✅ |
| Blower byte 14 bit 0x08 | ✓ | [rs485.py:79](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L79) `MASK_BULLEUR = 0x08` | ✅ |
| Heater byte 14 bit 0x10 | ✓ | [rs485.py:81](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L81) `MASK_CHAUFFAGE = 0x10` | ✅ |
| Circulation byte 17 bit 0x80 | ✓ | [rs485.py:78](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L78) `MASK_FILTRATION = 0x80` | ✅ |
| Command prefix `01 30` | ✓ | All frames in [rs485.py:37-46](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L37-L46) | ✅ |
| Light ON/OFF (17-byte) | ✓ | Verified: `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 81/80` | ✅ |
| Setpoint command | ✓ | [crc.py:108-127](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/crc.py#L108-L127) matches documented layout | ✅ |
| All Off (8-byte) | ✓ | [rs485.py:46](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L46) `01 30 08 3C AA 00 02 13` | ✅ |
| Filter schedule command | ✓ | [rs485.py:45](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L45) matches type `0xA4` | ✅ |
| CRC algorithm | ✓ | [crc.py](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/crc.py) same polynomial, word swap | ✅ |

### 🟡 P23/P20 Items with [✨] Status

The following items are marked `[✨]` (derived/inferred) in the protocol.md. Based on the P23 repo, here's the actual evidence level:

| Item | Status | Evidence |
|---|---|---|
| Heater Active (byte 14 bit 0x04) | [✨] | P23 code only checks bit `0x10` (enabled), not `0x04`. No capture evidence for `0x54`/`0x55` states. Reasonable inference. |
| Ozone Config Mode (byte 13 bit 0x80) | [✨] | P23 repo has NO ozone code at all. "Confirmed supported in P23 manuals" — verified by community posts, not code. Fair to keep [✨]. |
| Heater Config Mode (byte 13 bit 0x10) | [✨] | P23 repo has NO heater mode code. Same status as ozone config. Fair [✨]. |
| Ozone Active (byte 14 states 0x41/0xC1) | [✨] | No ozone support in P23 code. Inferred from P25 byte mapping. Fair [✨]. |
| Ozone Relay (byte 28 bit 0x20) | [✨] | No byte 28 parsing in P23 code. Pure inference. Fair [✨]. |
| Heat/Filter schedules (bytes 19-36) | [✨] | P23 code has a hardcoded filter schedule command ([rs485.py:45](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L45)) with type `0xA4`. The filter schedule **command** is [✅] confirmed. Broadcast schedule parsing is not implemented but structurally likely to match. |
| Manual Heater/Ozone Toggle commands | [✨] | Not implemented in P23 code. Reasonable inference. |
| DateTime command | [✨] | Not implemented in P23 code. Reasonable inference. |
| Heat Schedule command | [✨] | Not implemented in P23 code. Reasonable inference. |

### 🟡 P23 Filter Schedule Command Status

Line 139 marks the P23 filter schedule command as `[✅]` with an example: `... A4 00 A1 62 05 00 16 00 17 00 06 00`. This is confirmed by the P23 code ([rs485.py:45](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/p23b32_repo/custom_components/joyonway_p23b32/rs485.py#L45)): `0130103ca400a1620500160017000600` — the example payload **matches exactly**. ✅ Correct.

## Summary of Required Fixes

| # | Severity | Line | Fix |
|---|---|---|---|
| 1 | 🔴 Error | 134 | P25 setpoint: `80 80` → `80 98` |
| 2 | 🔴 Error | 131-133 | P23 pump/blower: 14-byte → 16-byte payloads (add `00 A1` at bytes 5-6) |
| 3 | 🔴 Error | 133 | P25 blower ON: `04 04` → `04 0C` |
| 4 | 🟡 Inaccuracy | 40 | P23 unescape: "indices 53+" → "indices 55+", "0–52" → "0–54" |
| 5 | 🟡 Missing | — | P25 behavioral notes (auto-off, panel-local, color mode, setpoint echo) |
| 6 | 🟡 Ambiguity | 135 | P25 heater uses Phase 6 variant, code uses Session 1 variant |
| 7 | 🟡 Misleading | 98 | P25 blower: primary source is byte 28, not byte 14 |
