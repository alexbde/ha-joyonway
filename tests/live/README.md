# Live Tests

This folder contains the **manual, hardware-dependent** unified live verification suite for the Joyonway spa RS485 protocol.

These tests are designed to be run directly against the hardware via the TCP-to-RS485 bridge to bypass the Home Assistant UI and verify raw wire commands.

## Unified Live Verification Suite

`test_spa_controls.py`

- Interactive CLI menu supporting targeted feature verification.
- Offline dry-run simulator option via `--dry-run` to validate control frame parsing, formatting, and test logic flow without hardware.
- Log files and binary wire frame captures are automatically generated under `artifacts_schedule_matrix/`.

### Test Suites Included:
1. **Basic Control Tests:** Verifies toggles and broadcast state confirmation for Lights, Blowers, Jets (pump cycles OFF → LOW → HIGH → OFF), and Temperature Setpoint modifications.
2. **Complete Schedule Matrix Tests:** Performs a comprehensive 50-case grid test covering all enable state combinations and time adjustments for both Heating and Filtration schedules, verifying correct bitmask flags and convergence before restoring original schedules.
3. **Ozone Control Tests:** Toggles Ozone modes (Manual vs. Auto) and confirms manual ozone controls, including a lock/behavior verification under Auto mode.
4. **Clock Drift & Auto-Sync Tests:** Forces a temporary datetime drift (90 seconds into the future) and triggers sync calculations to restore time to match system clock, validating correct year offset transmission.
5. **IntentQueue Coalescing & Cooldown Tests:** Validates queue coalescing (cancelling out contradictory rapid toggles), no-op logic, and mandatory command execution cooldown spacing (>= 1.0s).
6. **TCP Connection Drop & Grace Availability Tests:** Simulates a physical TCP drop, verifying the coordinator's exponential reconnection backoff scheduling and the 10-second entity availability grace timer.
7. **Low-Level Date & Time Write Test:** Tests time-only clock writes (prefix `0x50`) vs. full date & time clock writes (prefix `0x05`) to verify physical board compatibility.


### How to Run:

Make sure your virtual environment is active:
```bash
source .venv/bin/activate
```

#### Run in Simulation Mode (Dry-Run):
```bash
python tests/live/test_spa_controls.py --dry-run
```

#### Run on Physical Hardware:
1. Ensure your `.env` file at the root has your TCP bridge host and port configured:
   ```env
   SPA_BRIDGE_HOST=192.168.1.150
   SPA_BRIDGE_PORT=8899
   ```
2. Run the script:
   ```bash
   python tests/live/test_spa_controls.py
   ```
   Select `0` to execute all verification tests, or select a specific number to run a targeted test suite. Any other input exits the menu.




