# Joyonway P25B37-2032 Protocol Analysis

This document consolidates findings, signatures, and operational notes for the **Joyonway P25B37-2032** controller, based on captures and feedback from `@tprommi` (Post #186) and `@c0mpleX` (Post #63) in the community thread.

## 1. Hardware Details

* **Motherboard:** P25B37-2032 (Board version 1.8)
* **Topside Panel:** PB554 (Panel version 1.8, address `0x20`)
* **Associated Users:** `@tprommi` (active integration tester), `@c0mpleX` (first to share ESP32 captures).
* **Network Gateway:** Waveshare ESP32-S3-6CH-Relay (used by tprommi as a custom RS485-to-WiFi bridge running a TCP socket server).

---

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

---

## 3. Command Execution Issues (Writing Commands)

While reading the spa state works, `@tprommi` reports that **outbound command execution does not work** for the pump and lights when sending P25B85 command frames.

### 3.1. Analysis of Command Failure
The P25B85 command layout uses:
* **Command Header:** `01 20 10 3C [Type] 10 A1`

The failure of this command on the P25B37 highlights a potential protocol discrepancy:
1. **Source / Destination Index 5 Offset:** The P25B85 uses `0x10` at index 5. The P25B37 controller might expect `0x00` (similar to the P23B32: `01 30 10 3C [Type] 00 A1` or `01 20 10 3C [Type] 00 A1`).
2. **Keypad Source Address:** Although the PB554 touchpad usually responds on address `0x20`, if the P25B37 board runs a multi-equipment variant firmware, it might expect panel commands using a source ID of `0x30` (`01 30 10 3C ...`) or another specific address.
3. **Toggle vs. Discrete Commands:** The P25B85 uses a toggle command for lights. The P25B37 might require a discrete ON/OFF command similar to the P23B32/P20B29 (e.g. byte 16 = `0x81` or `0x80`).

## 4. Next Diagnostic Steps for @tprommi
To resolve the command mapping, tprommi needs to capture command frames generated directly by the physical PB554 touchpad:
1. Run a sniffer (`nc YOUR_IP 8899 | xxd`) while physically pressing the **Light** or **Jets** button on the panel.
2. Note the captured frame prefix and payload bytes.
3. Compare the prefix with `01 20 10 3C ...` to identify the addressing difference.
