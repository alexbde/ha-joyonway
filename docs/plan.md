# Implementation Plan - Integrate P20B29 Controller Model

This plan outlines the steps required to integrate the Joyonway P20B29 spa controller model into this integration, utilizing the protocol telemetry captured by Yannick in issue #65.

## Proposed Changes

### Adapter Layer & Heater Status Refactoring

To support custom base offsets cleanly for P20B29 (idle base offset `0x20`) and P25B37 (idle base offset `0x00`), we will refactor the heater status mapping to be model-specific.

#### [MODIFY] [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py)
- Update the `ModelAdapter` protocol to include the `heater_state_map: dict[int, str]` class attribute.

#### [MODIFY] [p25.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py)
- Add the `heater_state_map` class attribute to `P25BaseAdapter` using standard values:
  ```python
  heater_state_map = {
      0x40: "off",
      0x50: "standby",
      0x51: "circulation",
      0x55: "heating",
      0x54: "heating",
      0x41: "ozone",
      0xC1: "ozone",
  }
  ```
- Update `parse_status` in `P25BaseAdapter` to reference `self.heater_state_map` instead of the global `HEATER_STATE_MAP`.
- Update the `heater_active` and `ozone_active` parsing logic in `parse_status` to determine states dynamically using `self.heater_state_map.get(heater_base) == "heating"` and `self.heater_state_map.get(heater_base) == "ozone"` respectively, removing hardcoded module-level constants.
- [Nice-to-Have] Override `heater_state_map` in `P25B37Adapter` to support `0x00` base offsets:
  ```python
  heater_state_map = {
      0x00: "off", # or "standby"
      0x01: "circulation",
      0x04: "heating",
      0x05: "heating",
  }
  ```

#### [MODIFY] [p23.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py)
- Add the `heater_state_map` class attribute to `P23BaseAdapter` using the standard `0x40`/`0x50` map.
- Update `parse_status` in `P23BaseAdapter` to reference `self.heater_state_map` instead of the global `HEATER_STATE_MAP`.
- Update the `heater_active` and `ozone_active` parsing logic in `parse_status` to determine states dynamically using `self.heater_state_map.get(heater_base) == "heating"` and `self.heater_state_map.get(heater_base) == "ozone"` respectively, removing hardcoded module-level constants.

#### [NEW] [p20.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py)
Create a new adapter module for the P20 family:
- Defines `P20BaseAdapter` implementing the `ModelAdapter` protocol (analogous to the P23 implementation in `p23.py` as they share the same base command structure, framing, and payload mappings).
- Defines `P20B29Adapter` subclass.
- Uses logical signature byte `0x01` at logical offset 8 (`1A FF 01 3C D2 B4 FF 08 01`).
- Sets `has_blower = True`.
- Configures dual single-speed pumps (`jets_left`, `jets_right`).
- Configures `supported_light_colors`: `["auto", "red", "green", "yellow", "blue", "purple", "cyan", "white"]` (aligned with the P25B37).
- Defines a custom `heater_state_map` to support the `0x20` base offset:
  ```python
  heater_state_map = {
      0x20: "off", # or "standby"
      0x21: "circulation",
      0x24: "heating",
      0x25: "heating",
  }
  ```
- Overrides `parse_status` to extract light color index from byte 17 lower 4 bits, setting `"light_color_index"` and overriding `"light"` (True if index > 0).
- Overrides `build_light_command` to construct a 16-byte payload (without extra padding byte) using `btn_group=0x40`, `btn_action=0x40`, `context=0x04` and final byte `0x80 + color_index` as verified in physical captures.
- Overrides `build_ozone_manual_command` to use `btn_group=0x80`, `btn_action=0x80` (ON) / `0x00` (OFF) matching the verified P20B29 manual filtration/ozone button command mapping.

#### [MODIFY] [adapters init](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py)
Register the new model:
- Import `P20B29Adapter` from `.p20`.
- Add `"P20B29": P20B29Adapter` to `ADAPTERS`.

### Test Suite

#### [NEW] [test_p20b29_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p20b29_adapter.py)
Create unit tests for the P20B29 adapter:
- Test status parsing with Yannick's unescaped idle (`0x20` base) and active broadcast captures.
- Test command building (`build_light_command` with colors, `build_jets_command`, etc.).

## Verification Plan

### Automated Tests
- Run Ruff checks and formatting:
  ```bash
  .venv/bin/ruff check custom_components/
  .venv/bin/ruff format --check custom_components/
  ```
- Run mypy type checking:
  ```bash
  .venv/bin/mypy custom_components/joyonway/
  ```
- Run pytest suite:
  ```bash
  .venv/bin/pytest -q -W ignore
  ```
