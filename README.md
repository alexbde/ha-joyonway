<div align="center">

# Joyonway P25B85 Spa for Home Assistant

**Local read-only integration for the Joyonway P25B85 spa controller via RS485 over an Elfin EW11 WiFi bridge.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/alexbde/ha-joyonway-p25b85?style=for-the-badge&color=blue)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1.0%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

</div>

---

## Overview

This integration brings **read-only monitoring** of a **Joyonway P25B85** spa controller into Home Assistant. Communication is purely local via RS485, bridged to your network through an **Elfin EW11** (or similar) WiFi-to-RS485 adapter in TCP server mode. No cloud, no internet required.

The P25B85 controls spas like the **Home Deluxe White Marble** outdoor whirlpool and similar rigid/hardshell hot tubs with a PB554 colour touchpad.

> **Status: Read-only monitoring validated** — the P25B85 byte map has been
> checked against local RS485 captures for temperature, setpoint, pump, light,
> heater stages, and UV/ozone activity. Write commands remain intentionally
> disabled until command frames are captured and verified safely.

> **Discussion thread:** [JoyOnWay Spa Control — Home Assistant Community](https://community.home-assistant.io/t/joyonway-spa-control/582344)

---

## My Hardware

| Component | Details |
|-----------|---------|
| **Spa** | Home Deluxe White Marble (outdoor whirlpool, rigid/hardshell) |
| **Controller** | Joyonway P25B85, PCB `P2325B0003 R05` |
| **Touchpad** | PB554 colour screen |
| **RS485 Bridge** | Elfin EW11, RS-485 → WiFi, TCP server mode |
| **UART** | 38400 baud, 8N1 |
| **Pump** | 1× dual-speed (low = filtration, high = massage jets) |
| **Light** | RGB LED (9 colour states via button press) |
| **Heater** | 2 kW resistive, thermostat-controlled |
| **UV/ozone** | UV lamp connected on ozonator port |

---

## Features (read-only)

- **Water temperature** and **setpoint** monitoring (°C)
- **Pump state** — low speed (filtration) and high speed (jets)
- **Light** on/off status
- **Heater state** — circulation / heating / cooldown / UV-ozone
- **UV/ozone** active indicator
- **Bridge connectivity** sensor
- **Diagnostic sensors** for raw byte values (disabled by default)
- Fully local, no cloud, no internet
- English, French, and German UI translations

### What this integration does NOT do (yet)

- ❌ No write commands (no buttons to toggle equipment)
- ❌ No setpoint control
- ❌ No synthetic RS485 frame construction

Write support requires capturing verified command frames from the physical touchpad and is planned for a future phase.

---

## Safety Philosophy

The P25B85 uses a 4-byte CRC on all frames. KDy documented that **sending a frame with an invalid CRC can activate the heater unexpectedly**. Therefore:

- ❌ We NEVER send frames with forged/guessed CRC
- ❌ We NEVER construct synthetic command payloads
- ✅ Write support will ONLY replay frames captured verbatim from the physical panel
- ✅ Each command frame must be validated against an observed state change

---

## Requirements

| Item | Details |
|------|---------|
| Spa controller | Joyonway P25B85 with PB554 touchpad |
| RS485 bridge | Elfin EW11, USR-W610, or any RS485-to-TCP bridge |
| Bridge config | 38400 baud, 8N1, TCP Server mode, port 8899 |
| Home Assistant | 2024.1.0 or later |
| Network | HA and bridge on the same LAN |

---

## Installation

### Via HACS (recommended)

1. Open **HACS** in Home Assistant
2. Click ⋮ (top right) → **Custom repositories**
3. Repository URL: `https://github.com/alexbde/ha-joyonway-p25b85`
4. Category: **Integration**
5. Click **Add**, then find **Joyonway P25B85 Spa** and install
6. **Restart Home Assistant**
7. Go to **Settings → Devices & Services → Add Integration → "Joyonway P25B85"**

### Manual

1. Copy `custom_components/joyonway_p25b85/` into your HA `config/custom_components/` folder
2. Restart Home Assistant
3. Add the integration via the UI

---

## Configuration

After restart, go to **Settings → Devices & Services → Add integration** and search for **Joyonway P25B85**.

| Field | Value |
|-------|-------|
| IP address | The IP of your RS485 bridge on the local network |
| TCP port | `8899` (default) |

The integration performs a TCP connection test before saving.

> **⚠️ Single-client limitation:** Most RS485 bridges only accept one TCP connection at a time. Stop the phone app or other tools before using HA.

---

## Entities

### Sensors

| Entity | Description |
|--------|-------------|
| Water temperature | Current water temp in °C |
| Setpoint | Target temperature in °C |
| Heater state | circulation / heating / cooldown / uv_ozone |
| Spa clock | Controller date/time (diagnostic, disabled by default) |
| Raw pump byte | Diagnostic (disabled by default) |
| Raw heater byte | Diagnostic (disabled by default) |

### Binary sensors

| Entity | Description |
|--------|-------------|
| Pump low (filtration) | Low-speed pump active |
| Pump high (jets) | High-speed pump active |
| Light | Light on/off |
| Heater active | Heating element drawing power |
| UV/ozone | UV/ozone system active |
| RS485 bridge connection | TCP connectivity to bridge |

---

## Development Plan

This integration is built in phases:

| Phase | Status | Description |
|-------|--------|-------------|
| 1. Capture tools | ✅ Done | CLI tools for guided RS485 capture and frame analysis |
| 2. Integration skeleton | ✅ Done | HA integration with adapter architecture, protocol parser, entities |
| 3. Capture & validate | ✅ Done | Local captures validated the P25B85 byte map |
| 4. Write commands | Planned | Replay verified panel frames for equipment control |
| 5. Polish & release | Planned | HACS validation, community testing, documentation |

---

## Related Projects

- **[ha-joyonway-p23b32](https://github.com/KnapTheBuilder/ha-joyonway-p23b32)** — HA integration for the P23B32 controller (by christopheknap)
- **[joyonway-frame-analyzer](https://github.com/KnapTheBuilder/joyonway-frame-analyzer)** — Browser-based frame analysis tool for all Joyonway models

---

## Credits

| Contributor | Contribution |
|-------------|--------------|
| **[KDy](https://community.home-assistant.io/u/kdy)** | Baud rate discovery (oscilloscope), P25B85 byte map, pseudo-escape mechanism, CRC safety warning |
| **[christopheknap](https://github.com/KnapTheBuilder)** | P23B32 HACS integration, command frame captures, frame analyzer tool |
| **[Gaet78](https://community.home-assistant.io/u/gaet78)** | P69B133 integration, 30s timing discovery |
| **[c0mpleX](https://community.home-assistant.io/u/c0mplex)** | Frame samples and community discussion |

---

## License

This project is released under the [MIT License](LICENSE).

<div align="center">

---

**Made for the Home Assistant community. 🧖‍♂️**

</div>
