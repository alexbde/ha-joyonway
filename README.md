<div align="center">

# Joyonway Spa for Home Assistant

**Local Home Assistant integration for Joyonway spa controllers via an RS485-to-IP bridge.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/alexbde/ha-joyonway?style=for-the-badge&color=blue)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.1.0%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

</div>

## Overview

This integration brings **local monitoring and control** of **Joyonway** spa controllers into Home Assistant. Communication is purely local via RS485, bridged to your home network through any standard RS485-to-IP Ethernet or WiFi bridge (operating in TCP server mode). No cloud connection, no internet required.

The integration is built with a **modular adapter pattern**, allowing community members to extend support for different controller models (like the P25B85, P23B32, and others) by defining their specific byte maps and command frame formats.

> **Status: Pre-release / testing** — P25B85 write commands (light, heater, blower, jets, temperature setpoint, schedules, clock sync) are verified and working. Safety measures are built-in: the integration never sends unsolicited command packets or writes automatically on startup. **Use at your own risk.**

> **Discussion thread:** [JoyOnWay Spa Control — Home Assistant Community](https://community.home-assistant.io/t/joyonway-spa-control/582344)

## Compatibility & Supported Hardware

This integration is designed around a modular model adapter interface to support multiple Joyonway controller models. Since these systems are highly model-specific, integration complexity varies depending on how similar a model's protocol is to the verified reference configuration.

### Support & Integration Effort Rating

Based on community reverse-engineering efforts and sibling codebases, we have mapped the estimated integration difficulty for popular Joyonway models:

| Controller Model | Touchpad Panel | UART Config | Support Status | Integration Difficulty & Assessment |
|---|---|---|---|---|
| **Joyonway P25B85** | PB554 (Colour) | 38400 8N1 | ✅ Supported | **Verified Reference Case:** 100% of read and write commands (pumps, blower, light, heater, setpoint, ozone, datetime schedules) are fully implemented and tested. |
| **Joyonway P23B32** | PB553 (Segment) | 38400 8N1 | ⏳ Extensible | **Low/Medium Effort:** Extremely similar protocol layout and logical framing. Uses the identical 4-byte CRC-32 algorithm. A user can easily write a new model adapter by mapping its custom byte boundaries and command flags. |
| **Joyonway P69B133** | PB562/PB563/PB565 | 38400 8N1 | ⏳ High Effort | **High Effort:** Advanced high-performance controller supporting up to four pumps. Uses a completely distinct framing structure, packet layout, timing boundaries, and command builder. |

### Verified Reference Configuration & Requirements

Below is one concrete, fully tested hardware configuration that is confirmed to work:

*   **Spa Model Example:** Home Deluxe White Marble (outdoor rigid/hardshell hot tub)
*   **Touchpad:** PB554 colour touchscreen
*   **Controller Pack:** Joyonway P25B85 (PCB `P2325B0003 R05`)
*   **Heater:** 2 kW resistive, thermostat-controlled
*   **Pumps & Blowers:** 1× dual-speed pump (Massage jets + filtration), optional air blower
*   **Ozone/UV:** Auto or Manual mode via com1
*   **RS485-to-IP Bridge:** Any standard RS485-to-IP Ethernet or WiFi bridge (e.g., Elfin EW11, USR-W610, Protoss) configured in **TCP Server mode** on port `8899` (configured as 38400 baud, 8N1, no parity).
*   **Home Assistant Requirements:** 2026.1.0 or later on a network with local access to the IP bridge.

## Features

- **Water temperature** monitoring (°C)
- **Setpoint temperature** monitoring (°C)
- **Thermostat control** (10°C to 40°C) with fast debounced slider writes, supporting HVAC modes (`HEAT`/`OFF`) to enable/disable the heater directly
- **Jets control** (0% / 50% / 100%) via speed percentage controls
- **Manual ozone** switch (CONFIG category) to toggle between Auto and Manual ozone mode, unlocking the **Ozone** ON/OFF switch
- **Manual heating** switch (CONFIG category) to toggle between Auto and Manual heating mode, unlocking the **Heater** ON/OFF switch
- **Light** on/off via toggle command
- **Blower (air bubbler, optional hardware)** on/off
- **Heat schedule** — 2 time slots with start/end times and enable/disable
- **Filter schedule** — 2 time slots with start/end times and enable/disable
- **Auto-sync clock** switch (CONFIG category) to automatically align the spa's internal clock when drift exceeds 30 seconds
- **Status sensor** — off / standby / circulation / heating / ozone (with dynamic icons)
- **Jets sensor** — off / low / high
- **Persistent TCP connection** — real-time state updates (~1–2 s), automatic reconnect with exponential backoff
- **Optimistic UI** — writable entities show immediate feedback; snap back if the spa reports a different state
- All commands built dynamically via cracked CRC-32 (no static replay tables)
- Fully local, no cloud, no internet
- English, French, and German UI translations

## Safety Philosophy

The verified Joyonway controllers use a 4-byte CRC-32 on all command frames. The CRC algorithm has been fully reverse-engineered (standard CRC-32 polynomial `0x04C11DB7` with word-swap preprocessing) and verified against physical captures.

- ✅ CRC algorithm implemented and verified — all commands built dynamically
- ✅ Every command uses computed CRC (no replay-only frames)
- ✅ All commands validated against observed state changes from physical captures
- ✅ Write pacing enforces a 1-second cooldown between commands
- ✅ Intent queue serializes and coalesces rapid user actions (no bus contention)
- ✅ Schedule writes require complete broadcast data and fail explicitly if prerequisites are missing
- ✅ State reversions are never silent — warnings logged if spa doesn't confirm

> **Note:** Early reverse-engineering reports indicated that sending a frame with an invalid CRC could activate the heater unexpectedly on certain setups. This integration calculates the math-correct CRC-32 dynamically for all commands, completely avoiding this hazard.

## Installation

### Via HACS (recommended)

1. Open **HACS** in Home Assistant
2. Click ⋮ (top right) → **Custom repositories**
3. Repository URL: `https://github.com/alexbde/ha-joyonway`
4. Category: **Integration**
5. Click **Add**, then find **Joyonway Spa** and install
6. **Restart Home Assistant**
7. Go to **Settings → Devices & Services → Add Integration → "Joyonway Spa"**

### Manual

1. Copy `custom_components/joyonway/` into your HA `config/custom_components/` folder
2. Restart Home Assistant
3. Add the integration via the UI

## Configuration

After restart, go to **Settings → Devices & Services → Add integration** and search for **Joyonway Spa**.

| Field | Value |
|-------|-------|
| IP address | The IP of your RS485 bridge on the local network |
| TCP port | Bridge listening port (typically `8899`) |

The integration performs a TCP connection test before saving.

> **⚠️ Connection note:** The Elfin EW11 supports up to 4 simultaneous TCP connections. Home Assistant uses one; you can still use debug/capture tools in parallel.

## Entities

### Sensors

| Entity            | Description                                                                |
|-------------------|----------------------------------------------------------------------------|
| Water temperature | Current water temp in °C                                                   |
| Setpoint          | Current target temperature in °C                                           |
| Status            | off / standby / circulation / heating / ozone (icon changes per state) |
| Jets     | off / low / high                                                           |
| Spa clock         | Controller date/time as timestamp sensor (diagnostic, disabled by default) |

### Diagnostic Sensors (Disabled by default)

These raw-byte telemetry sensors help troubleshoot connection states and reverse engineer unmapped registers:

| Entity                  | Description                                                                |
|-------------------------|----------------------------------------------------------------------------|
| Heater byte (raw)       | Raw byte 14 value shown as hex (e.g. `0x40`)                                |
| Pump byte (raw)         | Raw byte 12 value shown as hex (e.g. `0x00`)                                |
| Ozone mode byte (raw)   | Raw byte 13 value shown as hex (e.g. `0x80`)                                |
| Activity byte (raw)     | Raw byte 28 value shown as hex (e.g. `0x08`)                                |
| Light/cycle byte (raw)  | Raw byte 17 value shown as hex (e.g. `0x80`)                                |
| Frame length            | Logical post-unescape frame length in `bytes`                              |
| Unmapped bytes hash     | MD5 fingerprint hash of all currently unmapped broadcast frame registers   |

### Binary sensors

| Entity                  | Description                                  |
|-------------------------|----------------------------------------------|
| RS485 bridge connection | Strict TCP connectivity to bridge (disabled by default) |

### Switches

| Entity             | Description                                   |
|--------------------|-----------------------------------------------|
| Heater             | Heater manual ON/OFF (available when **Manual heating** is ON) |
| Ozone              | Ozone ON/OFF (available when **Manual ozone** is ON) |
| Light              | Light ON/OFF (toggle with state guard)        |
| Blower             | Air blower / air bubbler ON/OFF (optional hardware, disabled by default) |
| Manual ozone       | Toggle Ozone Mode between Auto and Manual (CONFIG category) |
| Manual heating     | Toggle Heater Mode between Auto (Manual Heating OFF) and Manual (Manual Heating ON) (CONFIG category) |
| Auto-sync clock    | Enable/disable automatic spa clock sync (CONFIG category) |
| Heat slot 1 / 2   | Enable/disable heating schedule slots         |
| Filter slot 1 / 2 | Enable/disable filtration schedule slots      |

### Fan

| Entity | Description                                                  |
|--------|--------------------------------------------------------------|
| Jets   | Pump control via speed percentages (0% / 50% / 100%) |

### Climate

| Entity     | Description                            |
|------------|----------------------------------------|
| Thermostat | Target setpoint control (10°C to 40°C) and heater armed state control via HVAC modes (`HEAT`/`OFF`) |

### Time

| Entity                       | Description                        |
|------------------------------|------------------------------------|
| Heat slot 1/2 start/end     | Heating schedule times (HH:MM)     |
| Filter slot 1/2 start/end   | Filtration schedule times (HH:MM)  |

## Contributions Welcome

This integration is built as a collaborative community-oriented project! We welcome all contributions, including:
- **Adding new adapters:** Write a new model adapter under `custom_components/joyonway/adapters/` to support controllers like the P23B32.
- **Reporting findings:** Help map undocumented registers or share command byte structures.
- **Improving performance:** Fix bugs, refactor entities, or improve UI translations.

### How to Help Reverse Engineer

If you have a different Joyonway spa controller model and want to help map its protocol or add support for its features, you can capture and share telemetry:

#### 1. Expose Raw Protocol Telemetry in HA
Go to the **Joyonway Spa** integration in Home Assistant, click **Entities**, and enable the diagnostic sensors:
- **Heater byte (raw)**
- **Pump byte (raw)**
- **Ozone mode byte (raw)**
- **Activity byte (raw)**
- **Light/cycle byte (raw)**
- **Frame length**
- **Unmapped bytes hash**

Perform actions on your physical spa touchpad (e.g. click jets, lights, or adjust thermostat) and note down which raw bytes change.

#### 2. Run the Developer Broadcast Byte Capture Tool
If you have terminal access, you can run our standalone developer utility to analyze unmapped registers in real time:

```zsh
# Run the analysis tool to capture 20 frames directly
SPA_BRIDGE_HOST="192.168.1.150" SPA_BRIDGE_PORT="8899" python3 tools/capture_unmapped_bytes.py --count 20
```

The tool will parse your controller's unmapped bytes and print a clean breakdown showing which byte positions are static vs. changing, their observed values, and your unique MD5 `unmapped_bytes_hash`.

#### 3. Run the Live Verification Suite
To see what works on your hardware and what might need adjustments, you can run the live test suite in either simulation/dry-run mode or directly against your hardware:

```zsh
# Run the verification suite offline in simulation mode
python3 tests/live/test_spa_controls.py --dry-run

# Run directly against your physical hardware (bridge host/port configured in .env)
python3 tests/live/test_spa_controls.py
```

This checks basic commands, schedule matrices, ozone controls, auto-sync, and connection drop resilience, generating a test summary showing compatibilities.

Please share these details, your test results, your spa model, and touchpad model on our [Community Discussion Thread](https://community.home-assistant.io/t/joyonway-spa-control/582344) or open a GitHub Issue!



## Development & Testing

Run all commands directly using the environment's python/pytest binaries to avoid manual source-approval steps:

```zsh
cd /path/to/ha-joyonway
python3.13 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/pytest -q -W ignore
```

Requires Python 3.13+. The `[test]` extra installs `pytest-homeassistant-custom-component` and all HA runtime dependencies.

### Live Verification Suite (optional)

To verify the integration controls against real spa hardware or simulate it offline:

```zsh
# Run in simulation/dry-run mode
.venv/bin/python tests/live/test_spa_controls.py --dry-run

# Run directly on the physical hardware bridge
.venv/bin/python tests/live/test_spa_controls.py
```

## Related Projects

- **[ha-joyonway-p23b32](https://github.com/KnapTheBuilder/ha-joyonway-p23b32)** — HA integration for the P23B32 controller (by christopheknap)
- **[joyonway-frame-analyzer](https://github.com/KnapTheBuilder/joyonway-frame-analyzer)** — Browser-based frame analysis tool for all Joyonway models

## Credits

| Contributor                                                  | Contribution                                                                                     |
|--------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| **[KDy](https://community.home-assistant.io/u/kdy)**         | Baud rate discovery (oscilloscope), initial P25B85 byte map, pseudo-escape mechanism, CRC safety warning, RS485 bus sync/collision avoidance discovery |
| **[christopheknap](https://github.com/KnapTheBuilder)**      | P23B32 HACS integration, command frame captures, frame analyzer tool                             |
| **[Gaet78](https://community.home-assistant.io/u/gaet78)**   | P69B133 integration                                                                              |
| **[c0mpleX](https://community.home-assistant.io/u/c0mplex)** | Frame samples and community discussion                                                           |

## Disclaimer

This project is **not affiliated with, endorsed by, or connected to Joyonway, Home Deluxe, or any of their subsidiaries or affiliates**. "Joyonway", "Home Deluxe", "White Marble", model numbers (P25B85, PB554, etc.), and any associated logos or product names are trademarks or registered trademarks of their respective owners. All product names, brand names, and images are used solely for identification and compatibility purposes.

This software is provided as-is, without warranty. **Use at your own risk.** The authors accept no liability for any damage to hardware, property, or persons resulting from the use of this integration.

## License

This project is released under the [MIT License](LICENSE).

<div align="center">

**Made for the Home Assistant community. 🧖‍♂️**

</div>
