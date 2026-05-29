<div align="center">

# Joyonway P25B85 Spa for Home Assistant

**Local Home Assistant integration for the Joyonway P25B85 spa controller via RS485 over an Elfin EW11 WiFi bridge.**

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/alexbde/ha-joyonway-p25b85?style=for-the-badge&color=blue)](LICENSE)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2024.1.0%2B-41BDF5.svg?style=for-the-badge&logo=home-assistant&logoColor=white)](https://www.home-assistant.io)

</div>

## Overview

This integration brings **local monitoring and control** of a **Joyonway P25B85** spa controller into Home Assistant. Communication is purely local via RS485, bridged to your network through an **Elfin EW11** (or similar) WiFi-to-RS485 adapter in TCP server mode. No cloud, no internet required.

The P25B85 controls spas like the **Home Deluxe White Marble** outdoor whirlpool and similar rigid/hardshell hot tubs with a PB554 colour touchpad.

> **Status: Pre-release / testing** — all write commands verified on the
> developer's physical spa (light, heater, blower, jets, temperature setpoint,
> heat/filter schedules, clock sync). Safety fixes applied: the integration
> never sends commands automatically on startup. **Use at your own risk.**

> **Discussion thread:** [JoyOnWay Spa Control — Home Assistant Community](https://community.home-assistant.io/t/joyonway-spa-control/582344)

## My Hardware

| Component        | Details                                                       |
|------------------|---------------------------------------------------------------|
| **Spa**          | Home Deluxe White Marble (outdoor whirlpool, rigid/hardshell) |
| **Controller**   | Joyonway P25B85, PCB `P2325B0003 R05`                         |
| **Touchpad**     | PB554 colour screen                                           |
| **RS485 Bridge** | Elfin EW11, RS-485 → WiFi, TCP server mode                    |
| **UART**         | 38400 baud, 8N1                                               |
| **Pump**         | 1× dual-speed (low = filtration, high = massage jets)         |
| **Light**        | RGB LED (9 colour states via button press)                    |
| **Heater**       | 2 kW resistive, thermostat-controlled                         |
| **Ozone port**   | Ozone/UV (Auto or Manual mode via RS485)                    |

## Features

- **Water temperature** monitoring (°C)
- **Setpoint temperature** monitoring (°C)
- **Thermostat control** (10°C to 40°C) with debounced slider writes
- **Jets control** (off/low/high) via fan preset modes
- **Ozone** manual on/off (available when mode set to Manual in options)
- **Light** on/off via toggle command
- **Heater** manual on/off
- **Blower** on/off
- **Heat schedule** — 2 time slots with start/end times and enable/disable
- **Filter schedule** — 2 time slots with start/end times and enable/disable
- **Clock sync** — manual button (auto-sync available via options, disabled by default)
- **Options flow** — ozone mode (Auto/Manual, synced with spa) and auto clock sync toggle
- **Status sensor** — off / circulation / heating / ozone (with dynamic icons)
- **Jets sensor** — off / low / high
- **Persistent TCP connection** — real-time state updates (~1–2 s), automatic reconnect with exponential backoff
- **Optimistic UI** — writable entities show immediate feedback; snap back if the spa reports a different state
- All commands built dynamically via cracked CRC-32 (no replay tables)
- Fully local, no cloud, no internet
- English, French, and German UI translations

### What this integration does NOT do

- ❌ Ozone control not yet live-tested
- ❌ Light colour mode control (may be panel-local only)

## Safety Philosophy

The P25B85 uses a 4-byte CRC-32 on all command frames. The CRC algorithm has been fully reverse-engineered (standard CRC-32 polynomial `0x04C11DB7` with word-swap preprocessing) and verified against 44 unique captured frames covering all command types.

- ✅ CRC algorithm implemented and verified — all commands built dynamically
- ✅ Every command uses computed CRC (no replay-only frames)
- ✅ All commands validated against observed state changes from physical captures
- ✅ Write pacing enforces a 1-second cooldown between commands

> **Note:** KDy documented that sending a frame with an invalid CRC can activate the heater unexpectedly. This integration uses the verified CRC algorithm for all commands.

## Requirements

| Item           | Details                                          |
|----------------|--------------------------------------------------|
| Spa controller | Joyonway P25B85 with PB554 touchpad              |
| RS485 bridge   | Elfin EW11, USR-W610, or any RS485-to-TCP bridge |
| Bridge config  | 38400 baud, 8N1, TCP Server mode, port 8899      |
| Home Assistant | 2024.1.0 or later                                |
| Network        | HA and bridge on the same LAN                    |

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

## Configuration

After restart, go to **Settings → Devices & Services → Add integration** and search for **Joyonway P25B85**.

| Field | Value |
|-------|-------|
| IP address | The IP of your RS485 bridge on the local network |
| TCP port | `8899` (default) |

The integration performs a TCP connection test before saving.

### Options

After setup, go to **Settings → Devices & Services → Joyonway P25B85 → Configure** to access options:

| Option | Default | Description |
|--------|---------|-------------|
| Ozone mode | Auto | **Auto**: ozone runs on its schedule only (ozone switch hidden). **Manual**: enables the ozone switch for RS485 control. Setting is synced with the spa — the integration reads the current mode from the broadcast on startup. |
| Auto-sync clock | OFF | Automatically syncs the spa clock when drift exceeds 30 seconds (1-hour cooldown). Disabled by default to avoid unsolicited writes. |

> **⚠️ Connection note:** The Elfin EW11 supports up to 4 simultaneous TCP connections. Home Assistant uses one; you can still use debug/capture tools in parallel.

## Entities

### Sensors

| Entity            | Description                                                                |
|-------------------|----------------------------------------------------------------------------|
| Water temperature | Current water temp in °C                                                   |
| Setpoint          | Current target temperature in °C                                           |
| Status            | off / circulation / heating / ozone (icon changes per state)           |
| Jets (Düsen)      | off / low / high                                                           |
| Spa clock         | Controller date/time as timestamp sensor (diagnostic, disabled by default) |

### Binary sensors

| Entity                  | Description                                  |
|-------------------------|----------------------------------------------|
| RS485 bridge connection | Strict TCP connectivity to bridge (disabled by default) |

### Switches

| Entity             | Description                                   |
|--------------------|-----------------------------------------------|
| Heater             | Heater manual on/off                          |
| Ozone              | Ozone on/off (shown only when mode = Manual; hidden in Auto) |
| Light              | Light on/off (toggle with state guard)        |
| Blower             | Air blower on/off (disabled by default)       |
| Heat slot 1 / 2   | Enable/disable heating schedule slots         |
| Filter slot 1 / 2 | Enable/disable filtration schedule slots      |

> **Schedule enable/disable** uses a dedicated flags byte in the command payload
> (cracked from Phase 6 RS485 captures). Slot times are preserved when toggling.

### Fan

| Entity | Description                                                  |
|--------|--------------------------------------------------------------|
| Jets   | Pump control via preset modes `low` / `high` (off supported) |

### Climate

| Entity     | Description                            |
|------------|----------------------------------------|
| Thermostat | Target setpoint control (10°C to 40°C) |

### Time

| Entity                       | Description                        |
|------------------------------|------------------------------------|
| Heat slot 1/2 start/end     | Heating schedule times (HH:MM)     |
| Filter slot 1/2 start/end   | Filtration schedule times (HH:MM)  |

### Button

| Entity     | Description                                           |
|------------|-------------------------------------------------------|
| Sync clock | Sends current HA time to spa controller (disabled by default) |

## Development Plan

Roadmap and session handoff live in `docs/plan.md`.

Current high-level status:

- Capture + byte-map validation: done (Phase 6 complete)
- Integration entities: all implemented (temp, pump, light, heater, blower, ozone, schedules)
- CRC cracking + protocol implementation: done (44/44 frames verified)
- All commands: dynamic frame generation via cracked CRC-32
- Schedule enable/disable: flags byte lookup table (cracked and implemented)
- Ozone control: mode synced via options flow, switch sends manual ON/OFF
- Resilient UI: persistent TCP connection, optimistic state, graceful reconnect
- Safety: no automatic writes on startup, schedule overwrite guards
- Next: live ozone test, then version bump and release

## Testing

```zsh
cd /path/to/ha-joyonway
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[test]"
pytest -q
```

Requires Python 3.12 (Home Assistant compatibility). The `[test]` extra installs
`pytest-homeassistant-custom-component` and all HA runtime dependencies.

## Related Projects

- **[ha-joyonway-p23b32](https://github.com/KnapTheBuilder/ha-joyonway-p23b32)** — HA integration for the P23B32 controller (by christopheknap)
- **[joyonway-frame-analyzer](https://github.com/KnapTheBuilder/joyonway-frame-analyzer)** — Browser-based frame analysis tool for all Joyonway models

## Credits

| Contributor                                                  | Contribution                                                                                     |
|--------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| **[KDy](https://community.home-assistant.io/u/kdy)**         | Baud rate discovery (oscilloscope), P25B85 byte map, pseudo-escape mechanism, CRC safety warning |
| **[christopheknap](https://github.com/KnapTheBuilder)**      | P23B32 HACS integration, command frame captures, frame analyzer tool                             |
| **[Gaet78](https://community.home-assistant.io/u/gaet78)**   | P69B133 integration, 30s timing discovery                                                        |
| **[c0mpleX](https://community.home-assistant.io/u/c0mplex)** | Frame samples and community discussion                                                           |

## License

This project is released under the [MIT License](LICENSE).

<div align="center">


**Made for the Home Assistant community. 🧖‍♂️**

</div>
