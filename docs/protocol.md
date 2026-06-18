# Joyonway Spa RS-485 Protocol Reference

This document serves as the canonical reference for the RS-485 serial communication protocol used by Joyonway spa controllers, extracted from physical touchpad-to-controller buses. It consolidates details for three controller families: the **P20 family** (P20B29 — paired with PB554/PB555 keypads), the **P23 family** (P23B32 — paired with PB554/PB555 keypads), and the **P25 family** (P25B37, P25B85 — paired with PB554 keypads).

> [!NOTE]
> Throughout this document, verification status markers indicate confidence level: [✅] hardware-confirmed, [✨] structurally derived, [❌] confirmed unsupported, [❓] unknown. See [Appendix A](#appendix-a-status-legend) for full definitions.

## 1. Physical Layer & Framing

The controllers communicate over half-duplex RS-485 using the following serial settings:
*   **Baud Rate:** 38400
*   **Data Bits:** 8
*   **Parity:** None
*   **Stop Bits:** 1

### 1.1. Frame Delimiters
All communication on the bus is wrapped in framing boundaries:

| Field | Value |
| :--- | :--- |
| **Start Delimiter** | `0x1A` |
| **End Delimiter** | `0x1D` |
| **Escape Byte** | `0x1B` |

### 1.2. Escape Decoding Table
To prevent payload bytes from colliding with delimiter control bytes, the protocol escapes certain byte values on the wire:

| Wire Pair | Decoded Byte |
| :--- | :--- |
| `1B 11` | `0x1A` |
| `1B 0B` | `0x1B` |
| `1B 13` | `0x1C` |
| `1B 14` | `0x1D` |
| `1B 15` | `0x1E` |

**Decoding Order:** Frame boundaries are processed on the raw wire bytes first by identifying the `0x1A` start delimiter and scanning for the `0x1D` end delimiter. Once the raw frame is isolated, escape decoding is applied to the payload.

## 2. Unescaping Policies

The unescaping behavior differs between controller firmware families, which is critical to avoid frame parsing issues:

*   **P20 family (P20B29):** Assumed to follow the same tail-only unescaping policy as the P23 family (`unescape_full_frame = False`). [✨]
*   **P23 family (P23B32):** Only tail bytes (indices 55 and higher) should be unescaped (`unescape_full_frame = False`). Unescaping indices 0–54 can corrupt payload parsing, because binary status bytes in the header may accidentally match escape sequences (e.g., a status byte of `0x1B` followed by `0x11` is not an escape code, but raw data). [✅]
*   **P25 family (P25B37 / P25B85):** The entire frame payload is unescaped before parsing (`unescape_full_frame = True`). [✅]

## 3. CRC-32 Specification

All command and broadcast frames carry a 4-byte CRC-32. This CRC-32 is used for both outbound command construction and inbound broadcast frame validation.

| Parameter | Value |
| :--- | :--- |
| **Polynomial** | `0x04C11DB7` (standard Ethernet CRC-32) |
| **Initialization Value** | `0xFFFFFFFF` |
| **XorOut Value** | `0x00000000` (None) |
| **Reflection** | No (MSB-first, lookup table driven) |
| **Message Input** | Unescaped payload bytes (any length; padded with trailing zeros to 4-byte boundary) |
| **Preprocessing** | Each 32-bit word is byte-swapped/reversed before the CRC calculation |
| **CRC Storage** | Little-endian, occupying the last 4 bytes of the unescaped inner frame |

### 3.1. Algorithm (Pseudocode)

```
function compute_crc(payload[0..N-1]):
    swapped = word32_swap(payload)   # reverse bytes within each 4-byte group
                                     # (short trailing chunk zero-padded to 4)
    crc = 0xFFFFFFFF
    for each byte b in swapped:
        crc = (crc << 8) XOR TABLE[((crc >> 24) XOR b) AND 0xFF]
        crc = crc AND 0xFFFFFFFF
    return crc
```

*Note: The **word swap** preprocessing is due to the hardware CRC peripheral on the touchpad's ARM Cortex-M MCU feeding little-endian words directly into the calculation.*

### 3.2. Inbound Validation
Broadcast frames are validated by computing the CRC over all unescaped bytes except the last 4 (the CRC field itself). If the calculated value does not match the received CRC, the frame is silently discarded.

### 3.3. Wire Frame Construction (Outbound)
```
inner = payload[16 or 17 bytes] + crc[4 bytes LE]
escaped = escape(inner)
wire = 0x1A + escaped + 0x1D
```

## 4. Broadcast Frames (State Bytes)

The controller sends periodic broadcast frames (~2/sec) to report the spa state to the touchpad display. Broadcast frames are prefixed with destination address `0xFF` and the family ID signature byte at index 8.

*   **P20 family Header Signature:** `1A FF 01 3C D2 B4 FF 08 01` (Family ID `0x01` at index 8) [✅]
*   **P23 family Header Signature:** `1A FF 01 3C D2 B4 FF 08 02` (Family ID `0x02` at index 8) [✅]
*   **P25 family Header Signature:** `1A FF 01 3C D2 B4 FF 08 03` (Family ID `0x03` at index 8) [✅]

### 4.1. Broadcast State Map (0-indexed logical frame positions)

| Functionality / Sensor | P20 Family | P23 Family | P25 Family | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Family ID** | Byte 8: `0x01` [✅] | Byte 8: `0x02` [✅] | Byte 8: `0x03` [✅] | Distinguishes controller family on the bus. |
| **Water Temp** | Byte 9 (`°F` integer) [✅] | Byte 9 (`°F` integer) [✅] | Byte 9 (`°F` integer) [✅] | Raw water temperature in Fahrenheit. |
| **Setpoint Temp** | Byte 16 (`°F` integer) [✅] | Byte 16 (`°F` integer) [✅] | Byte 16 (`°F` integer) [✅] | Target thermostat temperature in Fahrenheit. |
| **Light State** | Byte 17, bits `0x0F` (color index 1–8 = ON; 0 = OFF) [✅] | Byte 17, bit `0x01` [✅] | Byte 17, bits `0x0F` (color index 1–8 = ON; 0 = OFF) [✅] | P20/P25: lower 4 bits encode color preset index (see §4.2). P23: simple ON/OFF toggle bit. |
| **Pump/Jets 1** | Byte 12, bit `0x04` (Left Pump) [✅] | Byte 12, bit `0x04` (Left Pump) [✅] | Byte 12: `0x00`=OFF, `0x02`=LOW, `0x04`=HIGH [✅] | P20/P23 have independent single-speed pumps. P25 uses 2-speed cycle. |
| **Pump/Jets 2** | Byte 12, bit `0x10` (Right Pump) [✅] | Byte 12, bit `0x10` (Right Pump) [✅] | N/A | P20/P23 single-speed Right Pump. |
| **Blower State** | Byte 14, bit `0x08` [✅] | Byte 14, bit `0x08` [✅] | Byte 14, bit `0x08` [✅] | Blower ON/OFF. P25 also mirrors at Byte 28 bit 3. |
| **Heater Byte Base** | `0x20` [✅] | `0x40` [✅] | `0x40` [✅]. P25B37: `0x00` [✅] | Idle base value of byte 14 (with blower bit `0x08` masked out). Each family uses a different base offset. |
| **Heater Active (Heating)** | Byte 14, bit `0x04` [✅] | Byte 14, bit `0x04` (states `0x54`/`0x55`/`0xD4`/`0xD5`) [✨] | Byte 14, bit `0x04` (states `0x54`/`0x55`/`0xD4`/`0xD5`) [✅]. P25B37: state values [✨] | Heating element is actively ON. P20 bit-level check confirmed via working production script; full-byte state values pending capture. P25B37 state values pending capture (see [#57](https://github.com/alexbde/ha-joyonway/issues/57)). |
| **Heater Enabled (Armed)** | [❓] | Byte 14, bit `0x10` (states `0x50`/`0x54`/`0xD0`/`0xD4`) [✅] | Byte 14, bit `0x10` (states `0x50`/`0x54`/`0xD0`/`0xD4`) [✅]. P25B37: state values [✨] | Heater thermostat is armed/enabled. Note: Bit `0x10` is NOT set when ozone state `0x41`/`0xC1` is active. P20: no heating captures available. P25B37: base offset `0x00` means standby/heating state codes differ from P25B85 (pending capture). |
| **Circulation Pump** | Byte 17, bit `0x80` [✅] | Byte 17, bit `0x80` [✅] | Byte 17, bit `0x80` [✅] | Circle icon on touchpad. Set during heating & filtration/ozone cycles. |
| **Ozone Config Mode** | [❓] | Byte 13, bit `0x80` [✨] | Byte 13, bit `0x80` [✅] | Lock flag: `1` = Manual, `0` = Auto. P20 byte 13 is constant `0x6F` across all captures — bit mapping may differ. |
| **Heater Config Mode** | [❓] | Byte 13, bit `0x10` [✨] | Byte 13, bit `0x10` [✅] | Lock flag: `1` = Manual, `0` = Auto. P20 byte 13 is constant `0x6F` across all captures — bit mapping may differ. |
| **Ozone Active (Auto)** | Byte 14, bit `0x01` [✅] | Byte 14: state `0x41` [✨] | Byte 14: state `0x41` [✅] | P20: bit-level check confirmed in production script; full-byte state values pending. P23/P25: state machine value. |
| **Ozone Active (Manual)** | [❓] | Byte 14: state `0xC1` [✨] | Byte 14: state `0xC1` [✅] | Auto vs manual ozone state distinction unknown for P20. |
| **Ozone Relay** | [❓] | Byte 28, bit `0x20` [✨] | Byte 28, bit `0x20` [✅] | Physical ozone / UV hardware relay status. |
| **System Date & Time** | Bytes 53–58 [✨] | Bytes 53–58 [✅] | Bytes 53–58 [✅] | Unescaped tail: Year (+2000), Month, Day, Hour, Min, Sec. |
| **Heat Schedule Slot 1** | Bytes 19–22 [✨] | Bytes 19–22 [✨] | Bytes 19–22 [✅] | Start/End hours & minutes (Hour \| 0x40 if enabled). |
| **Heat Schedule Slot 2** | Bytes 23–26 [✨] | Bytes 23–26 [✨] | Bytes 23–26 [✅] | Start/End hours & minutes (Hour \| 0x40 if enabled). |
| **Filter Schedule Slot 1** | Bytes 29–32 [✨] | Bytes 29–32 [✨] | Bytes 29–32 [✅] | Start/End hours & minutes (Hour \| 0x40 if enabled). |
| **Filter Schedule Slot 2** | Bytes 33–36 [✨] | Bytes 33–36 [✨] | Bytes 33–36 [✅] | Start/End hours & minutes (Hour \| 0x40 if enabled). |


*Status markers explained in [Appendix A](#appendix-a-status-legend).*

### 4.2. Light Color Preset Index

Controllers that support color-addressable lights (P25 family, P20 family) encode the current light color as a 4-bit index in the lower nibble of broadcast byte 17. The same index mapping is used in command action bytes (see §5.1).

| Index | Byte 17 Value | Color | Command Action Byte |
| :--- | :--- | :--- | :--- |
| 0 | `0x00` | OFF | `0x80` |
| 1 | `0x01` | Auto (cycle) | `0x81` |
| 2 | `0x02` | Red | `0x82` |
| 3 | `0x03` | Green | `0x83` |
| 4 | `0x04` | Yellow | `0x84` |
| 5 | `0x05` | Blue | `0x85` |
| 6 | `0x06` | Purple | `0x86` |
| 7 | `0x07` | Cyan | `0x87` |
| 8 | `0x08` | White | `0x88` |

*   **P20B29:** Supports direct color control via action bytes `0x81`–`0x88`. [✅]
*   **P23B32:** Does not support color indexing. Light is a simple ON/OFF toggle at byte 17 bit `0x01`. [✅]
*   **P25B37:** Supports direct color control via action bytes `0x81`–`0x88`. [✅]
*   **P25B85:** Action bytes update the state register, but on spas with 2-wire color-cycling bulbs the physical bulb ignores the target color and cycles automatically. [✅]

## 5. Command Frames

Command frames are constructed with a payload (typically 16 or 17 bytes; 8 bytes for the All Off emergency command) and a 4-byte CRC-32. The common command header prefix defines the panel source address and variant byte per family.

*   **P20 family Command Prefix:** `01 30 10 3C [Type] 00 A1` [✅]
*   **P23 family Command Prefix:** `01 30 10 3C [Type] 00 A1` [✅]
*   **P25 family Command Prefix:** `01 20 10 3C [Type] 10 A1` [✅]

P20 and P23 share the same panel source address (`0x30`) and variant byte (`0x00`).

### 5.1. Command Payload Mappings (Unescaped Payload Bytes)

| Command Function | P20 Family | P23 Family | P25 Family | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Light Control** | **Discrete ON/OFF (16-byte):**<br>• ON/Auto: `01 30 10 3C A1 00 A1 00 00 40 40 02 04 00 00 81` [✅]<br>• OFF: `... 00 00 40 40 02 04 00 00 80` [✅]<br>• Colors: `... 00 00 40 40 02 04 00 00 [0x80+idx]` [✅] | **Discrete ON/OFF (17-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 81` [✅]<br>• OFF: `... 00 00 00 40 40 02 04 00 00 80` [✅] | **Discrete ON/OFF (16-byte):**<br>• ON: `01 20 10 3C A1 10 A1 00 00 40 40 00 40 00 00 81` [✅]<br>• OFF: `... 00 00 40 40 00 40 00 00 80` [✅]<br>• Colors (P25B37): `... 00 00 40 40 00 40 00 00 [0x80+idx]` [✅]<br><br>**Toggle Command (P25B85 Legacy):**<br>`... 00 00 40 40 00 C0 00 [setpoint] 00` [✅] | P20 uses 16-byte payload with action byte at index 15, structurally closer to P25. P23 uses 17-byte payload with action byte at index 16. |
| **Pump/Jets 1** | Identical to P23 [✅] | **Left Pump ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 06 04 00 00 02 04 00 00 00` [✅]<br>• OFF: `... A1 06 00 00 00 02 04 00 00 00` [✅] | **Cycle (OFF → LOW → HIGH):**<br>• LOW: `... 02 02 00 00 00 [context] ...` [✅]<br>• HIGH: `... 06 04 00 00 00 [context] ...` [✅]<br>• OFF: `... 04 00 00 00 00 [context] ...` [✅] | P20/P23 have independent pumps. P25 uses cycle transitions. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Pump/Jets 2** | Identical to P23 [✅] | **Right Pump ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 18 10 00 00 02 04 00 00 00` [✅]<br>• OFF: `... A1 18 00 00 00 02 04 00 00 00` [✅] | N/A | P20/P23 single-speed Right Pump. |
| **Blower Control** | Identical to P23 [✅] | **Discrete ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 04 04 02 04 00 00 00` [✅]<br>• OFF: `... A1 00 00 04 00 02 04 00 00 00` [✅] | **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 04 0C 00 [context] 00 [setpoint] 00` [✅]<br>• OFF: `... A1 10 A1 00 00 04 00 00 [context] 00 [setpoint] 00` [✅] | Context: `0xC0` for P25B85, `0x40` for P25B37. P25B37: `has_blower = False`. |
| **Setpoint Temperature** | Identical to P23 [✅] | **Direct Set (16-byte):**<br>`01 30 10 3C A1 00 A1 00 00 80 80 02 04 00 [temp_f] 00` [✅] | **Direct Set (16-byte):**<br>`01 20 10 3C A1 10 A1 00 00 80 98 00 [context] 00 [temp_f] 00` [✅] | P20/P23 use `0x80`. P25 uses variant byte `0x98`. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Manual Heater Toggle** | Expected same as P23 [✨] | **Expected ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 08 18 02 04 00 00 00` [✨]<br>• OFF: `... A1 00 00 08 11 02 04 00 00 00` [✨] | **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 08 18 00 [context] ...` [✅]<br>• OFF: `... A1 10 A1 00 00 08 11 00 [context] ...` [✅] | Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Manual Ozone Toggle** | **Discrete ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 80 80 00 00 02 04 00 00 00` [✅]<br>• OFF: `... A1 80 00 00 00 02 04 00 00 00` [✅] | **Expected ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 01 01 02 04 00 00 00` [✨]<br>• OFF: `... A1 00 00 01 10 02 04 00 00 00` [✨] | **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 01 01 00 40 ...` [✅]<br>• OFF: `... A1 10 A1 00 00 01 10 00 40 ...` [✅] | P20 uses bytes 7–8 (`0x80 0x80` ON / `0x80 0x00` OFF) instead. P23/P25 use `btn_group=0x01` at byte 9. Uses context `0x40` for both P25B85 and P25B37. |
| **Set System DateTime** | Expected same as P23 [✨] | **Expected DateTime Command (Type 0xA2):**<br>`01 30 10 3C A2 00 A1 [prefix] [yy] [mm] [dd] [hh] [mm] [ss] 00 00` [✨] | **DateTime Command (16-byte Type 0xA2):**<br>`01 20 10 3C A2 10 A1 [prefix] [yy] [mm] [dd] [hh] [mm] [ss] 00 00` [✅] | Prefix: `0x05` = date + time; `0x50` = time only. |
| **Heat Schedule Set** | Expected same as P23 [✨] | **Expected Schedule Command (Type 0xA3):**<br>`01 30 10 3C A3 00 A1 [flags] [slot1...] [slot2...]` [✨] | **Schedule Command (16-byte Type 0xA3):**<br>`01 20 10 3C A3 10 A1 [flags] [slot1...] [slot2...]` [✅] | Schedule flags and hours mapping matches P25. |
| **Filter Schedule Set** | Expected same as P23 [✨] | **Schedule Command (16-byte Type 0xA4):**<br>`01 30 10 3C A4 00 A1 [flags] [slot1...] [slot2...]` [✅]<br>Example: `... A4 00 A1 62 05 00 16 00 17 00 06 00` | **Schedule Command (16-byte Type 0xA4):**<br>`01 20 10 3C A4 10 A1 [flags] [slot1...] [slot2...]` [✅] | Staged slot hours: slot 1 start/end, slot 2 start/end. |
| **All Off Emergency** | Expected same as P23 [✨] | **Discrete (8-byte):**<br>`01 30 08 3C AA 00 02 13` [✅] | **Expected (8-byte):**<br>`01 20 08 3C AA 00 02 13` [❌] | Short emergency shutoff command. NOT supported by P25 family (verified via physical test). |
| **Factory Reset** | N/A | N/A | **Factory Reset (16-byte):**<br>`01 20 10 3C A1 10 A1 00 03 00 00 00 40 00 5F 00` [✅] | Resets the controller to factory settings (captured from PB554 setup menu). |

*Status markers explained in [Appendix A](#appendix-a-status-legend).*

## 6. CRC Derivation Notes

This section summarizes how the CRC parameters were derived and confirmed:

*   **Discovery of Word Swap:** Delta/linearity checks on captured command frames behaved like a CRC-family transform, but simple brute-forcing failed because byte positions did not align. The discovery that the payload is byte-reversed inside 32-bit words before processing resolved this and unlocked polynomial validation.
*   **Polynomial:** Exhaustive search over all 2^32 possible CRC-32 polynomials identified `0x04C11DB7` as the single working polynomial.
*   **XorOut Equivalence:** Early command-only analysis used `init=0x00000000` and `xor_out=0x552D22C8` for 16-byte payloads. These parameters are mathematically equivalent to the generalized `init=0xFFFFFFFF` / `xor_out=0x00000000` format when processing the exact same length of inputs. The generalized form is preferred as it functions correctly for arbitrary-length broadcast frame validation.

## 7. Behavioral Notes

*   **Light commands are discrete:** All controller families support discrete ON and OFF command frames. For P20 and P25, payload byte 15 acts as the light action byte (`0x80` = OFF, `0x81` = ON/Auto, `0x82`–`0x88` for colors). For P23, the action byte is at payload byte 16 within a 17-byte payload (`0x80` = OFF, `0x81` = ON, no color support). Note that P25B85 also supports/sends a legacy toggle-style command via context `0xC0` with `0x00` as the tail/action byte.
*   **Command context bytes vary by P25 model variant:** Command frames sent to P25 controllers contain a context byte at index 12. P25B85 uses `0xC0`, whereas P25B37 uses `0x40`. Specifying the incorrect context byte for a model variant will result in commands being ignored by the controller.
*   **Pump commands are state-dependent (P25):** The physical panel UI is a cycle (OFF → LOW → HIGH → OFF), and the controller's RS-485 transition commands reflect this. Direct commands for LOW → OFF and HIGH → LOW do not exist. The integration must execute sequenced transitions.
*   **Pump auto-off (P25):** Pump high speed auto-stops after 20 minutes (hardware timer).
*   **Setpoint byte echo (P25):** Byte index 14 in every button command is the CURRENT setpoint at time of capture, embedded as a state echo. The controller accepts commands regardless of this byte's value matching actual state.
*   **Panel-local settings (P25):** Auto Lock, Brightness, Screen Flip, the About / Diagnostics screens, and the Custom Modes menu (used to save up to 5 custom configurations) produce no RS485 command frames and no broadcast state changes. These parameters are stored and handled entirely locally by the display panel.
*   **Heater byte base offset varies by family:** Byte 14 functions as a combined heater/blower/ozone state register. The idle base value differs: `0x20` for P20, `0x40` for P23, `0x00` for P25B37, and `0x40` for P25B85. The blower bit (`0x08`) is additive on top of the base in all families. The heater relay bit (`0x04`) and ozone relay bit (`0x01`) follow the same bit positions, but the full state machine values (standby, circulation, heating, ozone) for P20 and P25B37 are pending capture verification (see [#57](https://github.com/alexbde/ha-joyonway/issues/57)).
*   **P20 ozone command differs from P23/P25:** The P20B29 manual ozone/filtration toggle uses bytes 7–8 (`0x80 0x80` ON, `0x80 0x00` OFF), whereas P23/P25 use bytes 9–10 (`0x01 0x01` ON, `0x01 0x10` OFF). This is a structural difference, not a simple byte value change.
*   **P20 byte 13 anomaly:** All captured P20B29 broadcast frames show byte 13 as constant `0x6F` (`01101111`). This does not match the P23/P25 bit-flag pattern where bit `0x80` = ozone manual mode and bit `0x10` = heater manual mode. The P20B29 may encode configuration state differently at byte 13, or these captures may represent a single configuration snapshot. Further investigation is needed.

## Appendix A: Status Legend

*   `[✅]` **Confirmed:** Tested and verified on physical hardware.
*   `[✨]` **Derived:** Structurally inferred; highly likely to align but pending hardware verification.
*   `[❌]` **Unsupported:** Confirmed not supported or ignored by physical hardware.
*   `[❓]` **Unknown:** Exist on spa but register/bit definition is currently unknown.
