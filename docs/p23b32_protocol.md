# P23B32 / P20B29 Protocol Reference

This document describes the RS-485 communication protocol structure and register map specific to the **Joyonway P23B32** and **P20B29** spa controllers.


## 1. System Specifications

* **Controller:** Joyonway P23B32 and Joyonway P20B29 (fully protocol-compatible)
* **Touchpad:** PB554 or PB555 color screen
* **Unescaping Policy:** Unescape only tail bytes (55+) where datetime is stored. This prevents corruption of status byte registers (0-54) that may overlap with escape byte sequences.


## 2. Broadcast Frames

The controller sends periodic broadcast frames to report the spa state, prefixed with destination `0xFF` and model ID `0x02` at byte index 8.

**Broadcast Frame Header Signature:**
```
1A FF 01 3C D2 B4 FF 08 02
```

### 2.1. Broadcast Byte Map
0-indexed logical frame positions (after tail unescaping):

| Byte | Content |
| :--- | :--- |
| **8** | Model ID (`0x02`) |
| **9** | Water temperature (°F, raw integer) |
| **12** | Pump status: bit 2 (`0x04`) = Left pump ON, bit 4 (`0x10`) = Right pump ON |
| **14** | Heater and Blower flags: bit 3 (`0x08`) = Blower active, bit 4 (`0x10`) = Heater active |
| **16** | Setpoint temperature (°F) |
| **17** | Light and Circulation flags: bit 0 (`0x01`) = Light active, bit 7 (`0x80`) = Circulation/Filtration pump active |
| **53–58**| Date/time: year, month, day, hour, minute, second |


## 3. Command Frames

Command frames are built with varying payload sizes (14, 16, or 17 bytes) and a 4-byte CRC.

**Common command header (bytes 0–6):**
```
01 30 10 3C [type] 10 A1
```
Where `[type]` = `A1` (Button), `A2` (DateTime), `A3` (Heat Schedule), `A4` (Filter Schedule).

### 3.1. Button Commands (type 0xA1)

All commands use discrete ON/OFF payloads rather than toggle frames.

#### Light Control (17-byte payload):
* **Light ON:** `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 81` (CRC `edbaa01d`)
* **Light OFF:** `01 30 10 3C A1 00 A1 00 00 00 40 40 02 04 00 00 80` (CRC `5a20cdc1`)

#### Pump Control (14-byte payload):
Left and right pumps are controlled independently via bytes 5-6:
* **Left Pump ON:** `01 30 10 3C A1 06 04 00 00 02 04 00 00 00` (CRC `8b3ee413`)
* **Left Pump OFF:** `01 30 10 3C A1 06 00 00 00 02 04 00 00 00` (CRC `08bd1033`)
* **Right Pump ON:** `01 30 10 3C A1 18 10 00 00 02 04 00 00 00` (CRC `40d12de0`)
* **Right Pump OFF:** `01 30 10 3C A1 18 00 00 00 02 04 00 00 00` (CRC `4cdfff63`)

#### Blower Control (14-byte payload):
Controlled via bytes 7-8:
* **Blower ON:** `01 30 10 3C A1 00 00 04 04 02 04 00 00 00` (CRC `0f7f1a76`)
* **Blower OFF:** `01 30 10 3C A1 00 00 04 00 02 04 00 00 00` (CRC `fcc2864f`)

#### Thermostat Setpoint Command (16-byte payload):
Uses action `0x80`, modifier `0x02`, context `0x04`, setting the Fahrenheit temperature in byte 14:
* **Setpoint Temperature:** `01 30 10 3C A1 00 A1 00 00 80 80 02 04 00 [temp_f] 00`


### 3.2. Configuration Commands (type 0xA4)

#### Filtration Schedule Set (16-byte payload):
* **Filtration Schedule Set:** `01 30 10 3C A4 00 A1 62 05 00 16 00 17 00 06 00` (CRC `fc7954c6`)
  Exposes filtration activation by setting slot 1 to `05:00-22:00` (enabled) and slot 2 to disabled (`flags = 0x62`).


### 3.3. System Commands

#### All Off (8-byte payload):
* **All Off:** `01 30 08 3C AA 00 02 13` (CRC `8ce4268b`)
