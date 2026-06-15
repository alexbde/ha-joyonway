# Joyonway Spa RS-485 Protocol Reference

This document serves as the canonical reference for the RS-485 serial communication protocol used by Joyonway spa controllers, extracted from physical touchpad-to-controller buses. It consolidates details for the **Joyonway P25B85** and **P25B37** (paired with PB554 keypads) and the **Joyonway P23B32 / P20B29** (paired with PB554/PB555 keypads).

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

*   **P25B85 / P25B37:** The entire frame payload is unescaped before parsing (`unescape_full_frame = True`).
*   **P23B32 / P20B29:** Only tail bytes (indices 55 and higher) should be unescaped. Unescaping indices 0–54 can corrupt payload parsing, because binary status bytes in the header may accidentally match escape sequences (e.g., a status byte of `0x1B` followed by `0x11` is not an escape code, but raw data).

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

The controller sends periodic broadcast frames (~2/sec) to report the spa state to the touchpad display. Broadcast frames are prefixed with destination address `0xFF` and the model ID signature byte at index 8.

*   **P25B85 / P25B37 Header Signature:** `1A FF 01 3C D2 B4 FF 08 03` (Model ID `0x03` at index 8)
*   **P23B32 / P20B29 Header Signature:** `1A FF 01 3C D2 B4 FF 08 02` (Model ID `0x02` at index 8)

### 4.1. Broadcast State Map (0-indexed logical frame positions)

| Functionality / Sensor | P25B85 Offset & Bits | P23B32 / P20B29 Offset & Bits | Notes & Alignment |
| :--- | :--- | :--- | :--- |
| **Model ID** | Byte 8: `0x03` [✅] | Byte 8: `0x02` [✅] | Distinguishes controller family |
| **Water Temp** | Byte 9 (`°F` integer) [✅] | Byte 9 (`°F` integer) [✅] | Raw water temperature |
| **Setpoint Temp** | Byte 16 (`°F` integer) [✅] | Byte 16 (`°F` integer) [✅] | Target thermostat temperature |
| **Light State** | Byte 17, bit `0x01` [✅] | Byte 17, bit `0x01` [✅] | Light ON/OFF state |
| **Pump/Jets 1** | Byte 12: `0x00`=OFF, `0x02`=LOW, `0x04`=HIGH [✅] | Byte 12, bit `0x04` [✅] | P25 uses 2-speed; P23 uses single-speed Left Pump |
| **Pump/Jets 2** | N/A | Byte 12, bit `0x10` [✅] | P23 single-speed Right Pump |
| **Blower State** | Byte 14, bit `0x08` [✅] | Byte 14, bit `0x08` [✅] | Blower ON/OFF (both models). P25 also mirrors at Byte 28 bit 3. |
| **Heater Active (Heating)** | Byte 14, bit `0x04` (states `0x54`/`0x55`/`0xD4`/`0xD5`) [✅] | Byte 14, bit `0x04` (states `0x54`/`0x55`/`0xD4`/`0xD5`) [✨] | Heating element is actively ON. |
| **Heater Enabled (Armed)** | Byte 14, bit `0x10` (states `0x50`/`0x54`/`0xD0`/`0xD4`) [✅] | Byte 14, bit `0x10` (states `0x50`/`0x54`/`0xD0`/`0xD4`) [✅] | Heater thermostat is armed/enabled in menus (whether currently heating or in standby). Note: Bit `0x10` is NOT set when ozone state `0x41`/`0xC1` is active. |
| **Circulation Pump**| Byte 17, bit `0x80` [✅] | Byte 17, bit `0x80` [✅] | Circle icon. Set during heating & filtration/ozone |
| **Ozone Config Mode**| Byte 13, bit `0x80` [✅] | Byte 13, bit `0x80` [✨] | Lock flag: `1` = Manual, `0` = Auto. Confirmed supported in P23 manuals. |
| **Heater Config Mode**| Byte 13, bit `0x10` [✅] | Byte 13, bit `0x10` [✨] | Lock flag: `1` = Manual, `0` = Auto. Confirmed supported in P23 manuals. |
| **Ozone Active (Auto/Scheduled)** | Byte 14: state `0x41` [✅] | Byte 14: state `0x41` [✨] | Logical state machine state for auto/scheduled ozone cycle. |
| **Ozone Active (Manual)** | Byte 14: state `0xC1` [✅] | Byte 14: state `0xC1` [✨] | Logical state machine state for manual ozone cycle. Note: The state is exactly `0xC1` (or `0x41` for auto), mutually exclusive with standard heating codes and without bit `0x10` set. |
| **Ozone Relay** | Byte 28, bit `0x20` [✅] | Byte 28, bit `0x20` [✨] | Physical ozone / UV hardware relay status. |
| **System Date & Time**| Bytes 53–58 [✅] | Bytes 53–58 [✅] | Unescaped tail: Year, Month, Day, Hour, Min, Sec |
| **Heat Schedule Slot 1**| Bytes 19–22 [✅] | Bytes 19–22 [✨] | Start/End hours & minutes (Hour \| 0x40 if enabled) |
| **Heat Schedule Slot 2**| Bytes 23–26 [✅] | Bytes 23–26 [✨] | Start/End hours & minutes (Hour \| 0x40 if enabled) |
| **Filter Schedule Slot 1**| Bytes 29–32 [✅] | Bytes 29–32 [✨] | Start/End hours & minutes (Hour \| 0x40 if enabled) |
| **Filter Schedule Slot 2**| Bytes 33–36 [✅] | Bytes 33–36 [✨] | Start/End hours & minutes (Hour \| 0x40 if enabled) |

**Status Legend:**
*   `[✅]` **Confirmed:** Tested and verified on physical hardware.
*   `[✨]` **Derived:** Structurally inferred; highly likely to align but pending hardware verification.
*   `[❌]` **Unsupported:** Confirmed not supported or ignored by physical hardware.
*   `[❓]` **Unknown:** Exist on spa but register/bit definition is currently unknown.

## 5. Command Frames

Command frames are constructed with a payload (typically 16 or 17 bytes; 8 bytes for the All Off emergency command) and a 4-byte CRC-32. The common command header prefix defines the panel source address (`0x20` for P25 family vs. `0x30` for P23 family).

*   **P25B85 / P25B37 Command Prefix:** `01 20 10 3C [Type] 10 A1`
*   **P23B32 / P20B29 Command Prefix:** `01 30 10 3C [Type] 00 A1`

### 5.1. Command Payload Mappings (Unescaped Payload Bytes)

| Command Function | P25 family Payload Layout | P23B32 / P20B29 Payload Layout | Status & Notes |
| :--- | :--- | :--- | :--- |
| **Light Control** | **Toggle Command (P25B85):**<br>`01 20 10 3C A1 10 A1 00 00 40 40 00 C0 00 [setpoint] 00` [✅]<br><br>**Discrete ON/OFF (P25B37):**<br>• ON: `01 20 10 3C A1 10 A1 00 00 40 40 00 40 00 00 81` [✅]<br>• OFF: `01 20 10 3C A1 10 A1 00 00 40 40 00 40 00 00 80` [✅] | **Discrete ON/OFF (17-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 81` [✅]<br>• OFF: `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 80` [✅] | P25B85 sends toggle; discrete commands are NOT supported by P25B85 (verified via physical test). P25B37 sends discrete commands. |
| **Pump/Jets 1** | **Cycle (OFF → LOW → HIGH):**<br>• LOW: `... 02 02 00 00 00 [context] ...` [✅]<br>• HIGH: `... 06 04 00 00 00 [context] ...` [✅]<br>• OFF: `... 04 00 00 00 00 [context] ...` [✅] | **Left Pump ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 06 04 00 00 02 04 00 00 00` [✅]<br>• OFF: `01 30 10 3C A1 00 A1 06 00 00 00 02 04 00 00 00` [✅] | P25 family uses cycle transitions; P23 has independent pump controls. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Pump/Jets 2** | N/A | **Right Pump ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 18 10 00 00 02 04 00 00 00` [✅]<br>• OFF: `01 30 10 3C A1 00 A1 18 00 00 00 02 04 00 00 00` [✅] | P23 specific second pump command. |
| **Blower Control** | **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 04 0C 00 [context] 00 [setpoint] 00` [✅]<br>• OFF: `... A1 10 A1 00 00 04 00 00 [context] 00 [setpoint] 00` [✅] | **Discrete ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 04 04 02 04 00 00 00` [✅]<br>• OFF: `01 30 10 3C A1 00 A1 00 00 04 00 02 04 00 00 00` [✅] | Blower commands. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Setpoint Temperature**| **Direct Set (16-byte):**<br>`01 20 10 3C A1 10 A1 00 00 80 98 00 [context] 00 [temp_f] 00` [✅] | **Direct Set (16-byte):**<br>`01 30 10 3C A1 00 A1 00 00 80 80 02 04 00 [temp_f] 00` [✅] | P25 uses variant byte `0x98`. P23 uses `0x80`. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Manual Heater Toggle**| **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 08 18 00 [context] ...` [✅]<br>• OFF: `... A1 10 A1 00 00 08 11 00 [context] ...` [✅] | **Expected ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 08 18 02 04 00 00 00` [✨]<br>• OFF: `01 30 10 3C A1 00 A1 00 00 08 11 02 04 00 00 00` [✨] | P25 heater toggle. Context: `0xC0` for P25B85, `0x40` for P25B37. |
| **Manual Ozone Toggle**| **Discrete ON/OFF (16-byte):**<br>• ON: `... A1 10 A1 00 00 01 01 00 40 ...` [✅]<br>• OFF: `... A1 10 A1 00 00 01 10 00 40 ...` [✅] | **Expected ON/OFF (16-byte):**<br>• ON: `01 30 10 3C A1 00 A1 00 00 01 01 02 04 00 00 00` [✨]<br>• OFF: `01 30 10 3C A1 00 A1 00 00 01 10 02 04 00 00 00` [✨] | ozone toggle. Uses context `0x40` for both P25B85 and P25B37. |
| **Set System DateTime** | **DateTime Command (16-byte Type 0xA2):**<br>`01 20 10 3C A2 10 A1 [prefix] [yy] [mm] [dd] [hh] [mm] [ss] 00 00` [✅] | **Expected DateTime Command (Type 0xA2):**<br>`01 30 10 3C A2 00 A1 [prefix] [yy] [mm] [dd] [hh] [mm] [ss] 00 00` [✨] | Prefix: `0x05` = date + time; `0x50` = time only. |
| **Heat Schedule Set** | **Schedule Command (16-byte Type 0xA3):**<br>`01 20 10 3C A3 10 A1 [flags] [slot1...] [slot2...]` [✅] | **Expected Schedule Command (Type 0xA3):**<br>`01 30 10 3C A3 00 A1 [flags] [slot1...] [slot2...]` [✨] | Schedule flags and hours mapping matches P25. |
| **Filter Schedule Set** | **Schedule Command (16-byte Type 0xA4):**<br>`01 20 10 3C A4 10 A1 [flags] [slot1...] [slot2...]` [✅] | **Schedule Command (16-byte Type 0xA4):**<br>`01 30 10 3C A4 00 A1 [flags] [slot1...] [slot2...]` [✅]<br>Example: `... A4 00 A1 62 05 00 16 00 17 00 06 00` | Staged slot hours: slot 1 start/end, slot 2 start/end. |
| **All Off Emergency** | **Expected (8-byte):**<br>`01 20 08 3C AA 00 02 13` [❌] | **Discrete (8-byte):**<br>`01 30 08 3C AA 00 02 13` [✅] | Short emergency shutoff command. NOT supported by P25 family (verified via physical test). |

**Status Legend:**
*   `[✅]` **Confirmed:** Tested and verified on physical hardware.
*   `[✨]` **Derived:** Structurally inferred; highly likely to align but pending hardware verification.
*   `[❌]` **Unsupported:** Confirmed not supported or ignored by physical hardware.
*   `[❓]` **Unknown:** Exist on spa but register/bit definition is currently unknown.

## 6. CRC Derivation Notes

This section summarizes how the CRC parameters were derived and confirmed:

*   **Discovery of Word Swap:** Delta/linearity checks on captured command frames behaved like a CRC-family transform, but simple brute-forcing failed because byte positions did not align. The discovery that the payload is byte-reversed inside 32-bit words before processing resolved this and unlocked polynomial validation.
*   **Polynomial:** Exhaustive search over all 2^32 possible CRC-32 polynomials identified `0x04C11DB7` as the single working polynomial.
*   **XorOut Equivalence:** Early command-only analysis used `init=0x00000000` and `xor_out=0x552D22C8` for 16-byte payloads. These parameters are mathematically equivalent to the generalized `init=0xFFFFFFFF` / `xor_out=0x00000000` format when processing the exact same length of inputs. The generalized form is preferred as it functions correctly for arbitrary-length broadcast frame validation.

## 7. Behavioral Notes

*   **Light command behavior differs by model:** The P25B85 panel sends the same toggle frame
    regardless of ON or OFF intent — the integration's entity layer handles no-op detection by
    tracking the current state. The P25B37, P23B32, and P20B29 controllers use distinct discrete
    ON and OFF command frames (for P25B37 payload byte 15: `0x81` = ON, `0x80` = OFF; for P23/P20 payload
    byte 16: `0x81` = ON, `0x80` = OFF). The adapter's `build_light_command(on: bool)` method abstracts this.
*   **Command context bytes vary by P25 model variant:** Command frames sent to P25 controllers contain
    a context byte at index 12. P25B85 uses `0xC0`, whereas P25B37 uses `0x40`. Specifying the incorrect
    context byte for a model variant will result in commands being ignored by the controller.
*   **Pump commands are state-dependent (P25):** The physical panel UI is a cycle (OFF → LOW → HIGH → OFF), and the controller's RS-485 transition commands reflect this. Direct commands for LOW → OFF and HIGH → LOW do not exist. The integration must execute sequenced transitions.
*   **Pump auto-off (P25):** Pump high speed auto-stops after 20 minutes (hardware timer).
*   **Setpoint byte echo (P25):** Byte index 14 in every button command is the CURRENT setpoint at time of capture, embedded as a state echo. The controller accepts commands regardless of this byte's value matching actual state.
*   **Panel-local settings (P25):** Auto Lock, Brightness, Screen Flip, and the About / Diagnostics screens produce no RS485 command frames and no broadcast state changes. These parameters are stored and handled entirely locally by the display panel.
*   **Light color mode:** The P25 family controllers (both P25B85 and P25B37) support direct color addressing via discrete action bytes (`0x81`–`0x88` at byte index 15 with context `0x40`), which updates the controller's internal state register (broadcast byte 17 lower 4 bits = color index 1–8; 0 is OFF). However, on spas equipped with standard 2-wire color-cycling bulbs (which cycle colors sequentially via power cut-and-restore), the physical bulb will simply turn ON in its default automatic color-cycling mode regardless of the targeted color index command.
