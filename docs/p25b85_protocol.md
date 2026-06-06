# P25B85 Protocol Reference

This document describes the RS-485 communication protocol structure and register map specific to the **Joyonway P25B85** spa controller.


## 1. System Specifications

* **Controller:** Joyonway P25B85 (PCB `P2325B0003 R05`)
* **Touchpad:** PB554 color screen
* **Unescaping Policy:** The entire payload must be unescaped before parsing (`unescape_full_frame = True`).


## 2. Broadcast Frames

The controller sends periodic broadcast frames (~2/sec) to report the spa state. These are long frames prefixed with destination `0xFF` and model ID `0x03` at byte index 8.

**Broadcast Frame Header Signature:**
```
1A FF 01 3C D2 B4 FF 08 03
```

### 2.1. Broadcast Byte Map
0-indexed logical frame positions (after full-frame unescaping):

| Byte | Content |
| :--- | :--- |
| **8** | Model ID (`0x03`) |
| **9** | Water temperature (°F, raw integer) |
| **12** | Pump/jets status: `0x00`=off, `0x02`=low, `0x04`=high (manual jets only) |
| **13** | Configuration mode flags: bit 7 (`0x80`) = Manual Ozone, bit 4 (`0x10`) = Manual Heating |
| **14** | Heater/blower flags (see state table below) |
| **16** | Setpoint temperature (°F) |
| **17** | Light/circulation flags: bit 0 = light ON, bit 7 (`0x80`) = circulation pump active |
| **19** | Heat slot 1 start hour + enable flag (hour \| 0x40 if enabled) |
| **20** | Heat slot 1 start minute |
| **21** | Heat slot 1 end hour |
| **22** | Heat slot 1 end minute |
| **23** | Heat slot 2 start hour + enable flag (hour \| 0x40 if enabled) |
| **24** | Heat slot 2 start minute |
| **25** | Heat slot 2 end hour |
| **26** | Heat slot 2 end minute |
| **28** | Activity flags: bit 3 = blower active, bit 5 (`0x20`) = circulation active |
| **29** | Filter slot 1 start hour + enable flag (hour \| 0x40 if enabled) |
| **30** | Filter slot 1 start minute |
| **31** | Filter slot 1 end hour |
| **32** | Filter slot 1 end minute |
| **33** | Filter slot 2 start hour + enable flag (hour \| 0x40 if enabled) |
| **34** | Filter slot 2 start minute |
| **35** | Filter slot 2 end hour |
| **36** | Filter slot 2 end minute |
| **53–58**| Date/time: year, month, day, hour, minute, second |

### 2.2. Byte 14 — Heater and Blower State Register

| Value | Meaning |
| :--- | :--- |
| `0x40` | Idle (heater off, blower off) |
| `0x48` | Blower active (base `0x40` + bit 3) |
| `0x50` | Heater enabled/armed — standby (waiting for temp drop) |
| `0x51` | Circulation — pump running pre/post heat (circle icon on panel) |
| `0x54` / `0x55` | Heater actively heating (flame icon on panel) |
| `0x41` / `0xC1` | Disinfection cycle (ozone) |
| `0x58` | Blower + heater standby (base `0x50` + bit 3) |

#### State Registration Notes:
* **Heater Standby (`0x50`):** The controller sets byte 14 to `0x50` when the heater is enabled (armed). This does not indicate the circulation pump is physically running; the spa can stay in standby for hours with 0W consumption, only transitioning to `0x55` when active heating begins.
* **Heating Cycle Active (`Byte 17 bit 7` / `Byte 28 bit 5`):** The circulation pump icon (circle icon) is active on the screen when `Byte 17 bit 7` (`0x80`) is set. This flag is active for the entire heating cycle (pre-heat circulation → active heating → post-heat circulation) and also during ozone/filtration cycles.
* **Byte 12 & Byte 14 Independence:** Byte 12 (jets/pump) reflects manual jets state only. During active circulation (e.g. `0x51` or `0x41`), the pump byte stays `0x00` unless the user manually activates jets. 


## 3. Command Frames

Command frames are built with a 16-byte payload and a 4-byte CRC. 

**Common command header (bytes 0–6):**
```
01 20 10 3C [type] 10 A1
```
Where `[type]` = `A1` (Button), `A2` (DateTime), `A3` (Heat Schedule), `A4` (Filter Schedule).

### 3.1. Button Commands (type 0xA1)

**Payload layout:**

| Bytes | Content |
| :--- | :--- |
| **0–6** | Header: `01 20 10 3C A1 10 A1` |
| **7** | Pump command byte 1 (transition encoding) |
| **8** | Pump command byte 2 (transition encoding) |
| **9** | Button group identifier |
| **10** | Button action / value |
| **11** | Modifier byte (usually `0x00`; `0x80` for ozone mode) |
| **12** | Context byte (usually `0xC0`; `0x40` for ozone manual mode) |
| **13** | Always `0x00` |
| **14** | Current setpoint (°F) at time of command |
| **15** | Always `0x00` |

#### Button Group & Action Map:

| Button / Action | byte[9] | byte[10] ON | byte[10] OFF | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Light** | `0x40` | `0x40` | same (toggle) | Same frame for ON/OFF |
| **Heater** | `0x08` | `0x18` / `0x08` | `0x11` / `0x00` | Two session variants observed; both work |
| **Blower** | `0x04` | `0x0C` | `0x00` | Distinct ON/OFF |
| **Temperature** | `0x80` | `0x98` | — | Target temperature set directly in byte 14 |
| **Ozone manual**| `0x01` | `0x01` | `0x10` | Distinct ON/OFF; ozone mode must be Manual |

#### Ozone Mode Commands (byte[9]=`0x00`, byte[11]=`0x80`):
* **Auto Mode:** byte[12]=`0xC0`
* **Manual Mode:** byte[12]=`0x40`

#### Heater Mode Commands (byte[9]=`0x00`, byte[11]=`0x40`):
* **Auto Mode:** byte[12]=`0x80`
* **Manual Mode:** byte[12]=`0xC0`

#### Pump Control Transitions (bytes 7-8):
The controller enforces state-dependent transition rules. It rejects transition commands that do not correspond to permitted states:

| Target Transition | byte[7] | byte[8] | Controller Behavior by Current State |
| :--- | :--- | :--- | :--- |
| **OFF → LOW** | `0x02` | `0x02` | • From **OFF**: transitions to **LOW**.<br>• From **HIGH**: hard-shuts down to **OFF**.<br>• From **LOW**: ignored. |
| **LOW → HIGH** | `0x06` | `0x04` | • From **LOW**: transitions to **HIGH**.<br>• From **OFF**: transitions to **HIGH**.<br>• From **HIGH**: ignored. |
| **HIGH → OFF** | `0x04` | `0x00` | • From **HIGH**: transitions to **OFF**.<br>• From **LOW** / **OFF**: ignored. |

* **LOW → OFF** cannot be achieved directly. The integration must transition **LOW → HIGH**, wait for feedback, then transition **HIGH → OFF**.
* **HIGH → LOW** cannot be achieved directly. The integration must transition **HIGH → OFF**, wait for feedback, then transition **OFF → LOW**.


### 3.2. DateTime Set Commands (type 0xA2)

**Payload layout:**

| Bytes | Content |
| :--- | :--- |
| **0–6** | Header: `01 20 10 3C A2 10 A1` |
| **7** | Prefix byte: `0x05` = date + time, `0x50` = time only |
| **8** | Year (offset from 2000, e.g. `0x1A` = 26 = 2026) |
| **9** | Month |
| **10** | Day |
| **11** | Hour (24h) |
| **12** | Minute |
| **13** | Second |
| **14–15**| `0x00` |


### 3.3. Schedule Configuration Commands (type 0xA3 / 0xA4)

Used to program Heat (`0xA3`) and Filter (`0xA4`) schedules.

**Payload layout:**

| Bytes | Content |
| :--- | :--- |
| **0–6** | Header: `01 20 10 3C [A3/A4] 10 A1` |
| **7** | Enable/write mode flags byte |
| **8** | Slot 1 start hour |
| **9** | Slot 1 start minute |
| **10** | Slot 1 end hour |
| **11** | Slot 1 end minute |
| **12** | Slot 2 start hour |
| **13** | Slot 2 start minute |
| **14** | Slot 2 end hour |
| **15** | Slot 2 end minute |

#### Enable State Flags (Pure Toggle Commands):
* **Both Enabled:** `0xAA`
* **Slot 1 Enabled, Slot 2 Disabled:** `0x62`
* **Slot 1 Disabled, Slot 2 Enabled:** `0x9A`
* **Both Disabled:** `0x52`

#### Time Write Flags (Time Edits):
To ensure the controller registers time changes on disabled slots, the following flags are used:
* **Both Enabled / Edit:** `0xAA`
* **Slot 1 Enabled, Slot 2 Disabled / Edit:** `0x6A`
* **Slot 1 Disabled, Slot 2 Enabled / Edit:** `0x9A`
* **Both Disabled / Edit:** `0x5A`


## 4. Captured Frame Examples

All frames below are complete wire-level hex frames:

### 4.1. Button Commands
* **Light toggle:** `1a0120103ca110a10000404000c0006200bcdb13931d`
* **Pump OFF → Low:** `1a0120103ca110a10202000000c0006200f138e94a1d`
* **Pump Low → High:** `1a0120103ca110a10604000000c000620070f8dce71d`
* **Pump High → OFF:** `1a0120103ca110a10400000000c0006200ffbdc5c81d`
* **Blower ON:** `1a0120103ca110a10000040400c0006200f4b922821d`
* **Blower OFF:** `1a0120103ca110a10000040000c00062000704bebb1d`
* **Heater ON:** `1a0120103ca110a10000081800c00062000dd6159b1d`
* **Heater OFF:** `1a0120103ca110a10000081100c0006200fac57ba71d`
* **Temperature to 100°F:** `1a0120103ca110a10000808000c0006400430ed65b1d`
* **Ozone manual ON:** `1a0120103ca110a100000101004000620060b46dea1d`

### 4.2. Configuration Commands
* **DateTime set:** `1a0120103ca210a1501b11051b0b090c0000001b0b16f6891d`
* **Heat schedule (both on):** `1a0120103ca310a1aa0c001000150016003efb8dd91d`
* **Filter schedule (both on):** `1a0120103ca410a1aa0b000d00110012007e2109021d`
