# Joyonway P20B29 Protocol Reference

This document gathers all captures, signatures, and verified command frames for the **Joyonway P20B29** controller, as discussed and captured by `@Yannickt26` in the community thread.

## 1. Hardware & Integration Details

* **Motherboard:** P20B29-2032 V183 (often labeled as a 1-pump single-speed controller tier, but running the "multi-equipment" firmware skeleton shared with the P23B32).
* **Topside Panel:** PB555 (address `0x30`).
* **Confirmed Gateways:** 
  * Originally USR-W610 (encountered intermittent lockups/dropouts).
  * Replaced with **ZLAN5143D** (proved highly stable under continuous polling/HA connection).
* **Serial Protocol Settings:** 38400 baud, 8 data bits, no parity, 1 stop bit (38400 8N1).

## 2. Framing & Signatures

The P20B29 conforms to the standard Joyonway 1A/1D family delimiters and tail-only pseudo-escape sequence unescaping.

### 2.1. Inbound Broadcast State Frame (B4)
* **Start Delimiter:** `0x1A`
* **Destination Address:** `0xFF`
* **Model ID Signature Byte:** `0x01` (index 8 of logical frame)
* **Logical Signature Prefix:** `1A FF 01 3C D2 B4 FF 08 01`

**Example Broadcast Capture (Unescaped):**
```
1a ff 01 3c d2 b4 ff 08 01 5d 04 d4 00 6f 20 00 63 00 02 49 1b 15 11 00 0c 1b 15 17 1b 15 00 00 0c 1b 15 17 1b 15 0c 1b 15 17 1b 15 00 00 fe 4f 00 00 00 00 00 00 00 00 00 00 00 00 1b 11 05 14 14 1b 11 1f 03 00 df 4e 83 aa 1d
```
* **Byte 8 (Model ID):** `0x01` (Distinguishes the P20B29 profile from P23B32 `0x02` and P25B85 `0x03`).
* **Byte 9 (Water Temp):** `0x5D` (Fahrenheit: 93°F / 33.9°C).
* **Byte 16 (Setpoint Temp):** `0x63` (Fahrenheit: 99°F / 37.2°C).
* **Bytes 53–58 (Unescaped DateTime):** `1b 11 05 14 14 1b 11 1f` -> escapes unescaped to year `26` (2026), month `5`, day `20`, hour `20`, minute `20` (from `1b 11` -> `1a` offset), second `31` (from `1b 11` -> `1a` offset + time diff).

---

## 3. Verified Command Frames (A1)

The P20B29 uses the exact same command prefix and payload mapping skeleton as the P23B32 controller, with panel source address `0x30`.

* **Command Prefix:** `01 30 10 3C A1 00 A1`

The following command frames (complete with CRC-32 and framing delimiters) have been verified to function on physical P20B29 hardware:

### 3.1. Pump 1 / Left Pump
* **Pump 1 ON:**
  `1a 01 30 10 3c a1 00 a1 06 04 00 00 02 04 00 00 00 8b 3e e4 13 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 06 04 00 00 02 04 00 00 00`
  * *CRC-32:* `8b 3e e4 13`
* **Pump 1 OFF:**
  `1a 01 30 10 3c a1 00 a1 06 00 00 00 02 04 00 00 00 08 bd 10 33 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 06 00 00 00 02 04 00 00 00`
  * *CRC-32:* `08 bd 10 33`

### 3.2. Pump 2 / Right Pump
* **Pump 2 ON:**
  `1a 01 30 10 3c a1 00 a1 18 10 00 00 02 04 00 00 00 40 d1 2d e0 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 18 10 00 00 02 04 00 00 00`
  * *CRC-32:* `40 d1 2d e0`
* **Pump 2 OFF:**
  `1a 01 30 10 3c a1 00 a1 18 00 00 00 02 04 00 00 00 4c df ff 63 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 18 00 00 00 02 04 00 00 00`
  * *CRC-32:* `4c df ff 63`

### 3.3. Blower
* **Blower ON:**
  `1a 01 30 10 3c a1 00 a1 00 00 04 04 02 04 00 00 00 0f 7f 1b 11 76 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 00 00 04 04 02 04 00 00 00`
  * *CRC-32:* `0f 7f 1a 76` (wire format escapes `0x1A` as `1B 11`)
* **Blower OFF:**
  `1a 01 30 10 3c a1 00 a1 00 00 04 00 02 04 00 00 00 fc c2 86 4f 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 00 00 04 00 02 04 00 00 00`
  * *CRC-32:* `fc c2 86 4f`

### 3.4. Thermostat Setpoint
* **Set Temp to 38°C (100°F):**
  `1a 01 30 10 3c a1 00 a1 00 00 80 80 02 04 00 64 00 96 20 61 e1 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 00 00 80 80 02 04 00 64 00` (temp byte `0x64` = 100)
  * *CRC-32:* `96 20 61 e1`
* **Set Temp to 10°C (50°F):**
  `1a 01 30 10 3c a1 00 a1 00 00 80 80 02 04 00 32 00 34 22 13 8e 1d`
  * *Unescaped Payload:* `01 30 10 3c a1 00 a1 00 00 80 80 02 04 00 32 00` (temp byte `0x32` = 50)
  * *CRC-32:* `34 22 13 8e`
