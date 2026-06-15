# Joyonway P25B37-2032 Protocol Analysis

This document consolidates findings, signatures, and operational notes for the **Joyonway P25B37-2032** controller, based on captures and feedback from `@tprommi` (Post #186 & Issue #57) and `@c0mpleX` (Post #63) in the community thread.

## 1. Hardware Details

* **Motherboard:** P25B37-2032 (Board version 1.8)
* **Topside Panel:** PB554 (Panel version 1.8, address `0x20`)
* **Associated Users:** `@tprommi` (active integration tester), `@c0mpleX` (first to share ESP32 captures).
* **Network Gateway:** ESP32-based bridge (used by tprommi as a custom RS485-to-WiFi bridge running a TCP socket server).

## 2. Inbound Broadcast State Frame (B4)

The P25B37 shares the same broadcast header signature and delimiters as the P25B85:

* **Header Signature:** `1A FF 01 3C D2 B4 FF 08 03` (Model ID `0x03` at index 8)
* **Status:** Fully compatible with the P25B85 broadcast parser.

### 2.1. Working Readings (Confirmed by @tprommi)

Because the broadcast frame follows the P25B85 structure, the following sensors decode correctly out of the box:
* **Water Temperature:** Offset 9 (Fahrenheit).
* **Setpoint Temperature:** Offset 16 (Fahrenheit).
* **Jets/Dusen State:** Offset 12 correctly reads Pump Level 0 (Off), Level 1 (Low), and Level 2 (High).
* **Light State:** Offset 17 bit `0x01` correctly displays the light status (ON/OFF).

### 2.2. Unmapped / Discrepant Readings

* **Status (`sensor.joyonway_p25b85_status`):** Always reports `unknown`. This indicates that the heater/logical state machine status byte (Offset 14) behaves differently on the P25B37 or broadcasts distinct values not matching the P25B85 states (`0x40`, `0x50`, `0x51`, `0x54`, `0x55`).
* **Auto Clock Sync:** Option has no effect; the PB554 display on the P25B37 setup has no system clock configuration menu.
* **Air Diffuser / Blower:** The P25B37 does not report a blower state matching the P25B85 blower bit definitions.

## 3. Command Analysis (Touchpad Captures from Issue #57)

`@tprommi` successfully ran the guided capture runbook and captured the actual RS-485 command packets transmitted by the physical PB554 touchpad. This resolved the control write discrepancies.

### 3.1. Command Header & Prefix Comparison

Both P25B85 and P25B37 commands use the same addressing layout:
* **Wire Format prefix:** `1A 01 20 10 3C A1 10 A1 ...`
* **Unescaped Payload prefix:** `01 20 10 3C A1 10 A1 ...`
  * Index 0: `0x01`
  * Index 1: `0x20` (touchpad source ID)
  * Index 2: `0x10` (controller destination ID)
  * Index 3: `0x3C` (type)
  * Index 4: `0xA1` (command type)
  * Index 5: `0x10` (variant)
  * Index 6: `0xA1` (echo of command type)

This confirms that the source, destination, and command addressing are identical to the P25B85.

### 3.2. Jets / Pump Controls

The P25B37 uses the exact same jet transition bytes (indices 7 & 8) as the P25B85:
* **LOW Speed:** `02 02` (Payload: `01 20 10 3C A1 10 A1 02 02 00 00 00 40 00 62 00`)
* **HIGH Speed:** `06 04` (Payload: `01 20 10 3C A1 10 A1 06 04 00 00 00 40 00 62 00`)
* **OFF**: `04 00` (Payload: `01 20 10 3C A1 10 A1 04 00 00 00 00 40 00 62 00`)

**Key Protocol Difference**:
* **Byte 12 (Context)**: The P25B85 uses `0xC0` for the context byte, whereas the P25B37 expects `0x40`. Using `0xC0` on the P25B37 causes the controller to ignore the command completely.

### 3.3. Light / LED Controls

Unlike the P25B85 which uses a toggle cycle command (Byte 9 = `0x40`, Byte 10 = `0x40`, Byte 12 = `0xC0`, Byte 15 = `0x00`), the P25B37 utilizes discrete ON/OFF commands and discrete values:
* **Light ON / Cycle Color:** `01 20 10 3C A1 10 A1 00 00 40 40 00 40 00 62 81`
  * Byte 9: `0x40` (btn group)
  * Byte 10: `0x40` (btn action)
  * Byte 12: `0x40` (context)
  * Byte 15: `0x81` (turns Light ON / transitions to automatic color cycle mode)
* **Light OFF:** `01 20 10 3C A1 10 A1 00 00 40 40 00 40 00 62 80`
  * Byte 9: `0x40`
  * Byte 10: `0x40`
  * Byte 12: `0x40`
  * Byte 15: `0x80` (turns Light OFF directly)

### 3.4. Analysis of Full Capture File & Light Color Hypothesis

Analysis of the full touchpad transitions capture file (`p25b37_touchpad_transitions_20260614_213323.txt`) confirms:
* **Command Frames**: The file only contains 6 unique command frames corresponding to the 7-step basic guided capture runbook. Specifically, the only Light commands sent were `0x81` (ON/Auto) and `0x80` (OFF).
* **Broadcast Frames**: The broadcast frames only contain `0x00` (Light OFF) and `0x01` (Light ON) at offset 17. The specific color state of the LED is not broadcasted at this offset (or is managed entirely local to the controller board).
* **Hypothesis on Colors**: When cycling colors (Auto -> Red -> Green -> Yellow -> Blue -> Purple -> Cyan -> White -> Off), the touchpad might either:
  1. Send a sequence of discrete command bytes at payload index 15 (e.g. `0x81` for Auto, `0x82` for Red, `0x83` for Green, etc.), which would make colors directly addressable.
  2. Or send the same `0x81` cycle command on every brief button press, meaning colors are cycled sequentially and not directly addressable.

To resolve this and map the protocol accurately, the guided capture script has been updated with a dedicated interactive color capture runbook. During this runbook, the script will open a 4-second capture window on each brief button press and allow the user to confirm or enter the active color (defaulting to the reported sequence: `red`, `green`, `yellow`, `blue`, `purple`, `cyan`, `white`, `off`).


## 4. Implementation Guidance for the P25B37 Adapter

When building the `P25B37Adapter`, the following modifications should be implemented:
1. **Inherit or derive** from the P25B85 parsing structure (since broadcast parsing is identical).
2. **Override the button command builder** to use `context = 0x40` instead of `context = 0xC0`.
3. **Re-implement `build_light_command(self, on: bool)`** to send the discrete `0x81` (ON) and `0x80` (OFF) values at payload index 15.
4. **Expose two-speed pump support** using the corrected context byte.
5. **Inspect the status byte (Offset 14)** in future captures to map the `sensor.joyonway_p25b85_status` enum states correctly for the P25B37.
