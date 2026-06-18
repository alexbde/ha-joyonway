# Implementation Plan - Integrate P20B29 Controller Model

This plan outlines the steps required to integrate the Joyonway P20B29 spa controller model into this integration, utilizing the protocol telemetry captured by Yannick in issue #65.

## Proposed Changes

### Adapter Layer & Heater Status Refactoring

To support custom base offsets cleanly for P20B29 (idle base offset `0x20`) and P25B37 (idle base offset `0x00`), we will refactor the heater status mapping to be model-specific.

#### [MODIFY] [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py)
- No changes to the `ModelAdapter` protocol. The `heater_state_map` is an internal parsing detail, not a protocol-level contract.

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
- Override `heater_state_map` in `P25B37Adapter` to support `0x00` base offset (confirmed via issue [#57](https://github.com/alexbde/ha-joyonway/issues/57) captures where `h=0x00` idle):
  ```python
  heater_state_map = {
      0x00: "off",  # or "standby"
      0x01: "circulation",
      0x04: "heating",
      0x05: "heating",
  }
  ```
  **Note:** These P25B37 state values are ✨ derived (bit offsets from P25B85 transposed to `0x00` base). Only `0x00` (idle) has been confirmed. Full heating/ozone captures from P25B37 hardware are pending (see [#57](https://github.com/alexbde/ha-joyonway/issues/57)).

#### [MODIFY] [p23.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py)
- Add the `heater_state_map` class attribute to `P23BaseAdapter` using the standard `0x40`/`0x50` map.
- Update `parse_status` in `P23BaseAdapter` to reference `self.heater_state_map` instead of the global `HEATER_STATE_MAP`.
- Update the `heater_active` and `ozone_active` parsing logic in `parse_status` to determine states dynamically using `self.heater_state_map.get(heater_base) == "heating"` and `self.heater_state_map.get(heater_base) == "ozone"` respectively, removing hardcoded module-level constants.

#### [NEW] [p20.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py)
Create a new, standalone adapter module for the P20 family.

> [!IMPORTANT]
> **Architecture Decision — One file per family, no cross-family inheritance.**
> `P20BaseAdapter` is implemented as a standalone class (not subclassing `P23BaseAdapter`), matching the existing pattern where each family file (`p25.py`, `p23.py`) is self-contained. While P20 and P23 share the same command prefix (`01 30`) and several byte-identical command builders (pumps, blower, setpoint, schedules, datetime), keeping them independent ensures:
> - **No fragile inheritance chains:** Changes to P23 cannot accidentally break P20.
> - **Full readability:** Each file is self-contained; no need to trace up an inheritance tree.
> - **Symmetry:** All three families follow the same structural pattern.
>
> The trade-off is ~100 lines of duplicated boilerplate (pump/blower/setpoint/schedule/datetime builders and `_build_button_command` helper). This code is stable and unlikely to change.

*   **Helper Method `_build_button_command`**:
    *   Constructs a 16-byte base command payload starting with `[0x01, 0x30, 0x10, 0x3C, A1, 0x00, A1, ...]` (where `0x30` is panel address, `0x00` variant byte) and wraps with CRC.
*   **Protocol Methods Implementation**:
    *   `parse_status(frame: bytes) -> dict | None`: Parses broadcast status. Sets `ozone_mode` and `heater_mode` to `None`. Extracts light color index from lower 4 bits of byte 17.
    *   `entity_descriptions() -> list[SpaEntityDescription]`: Returns standard sensors and entities (excluding manual mode switches).
    *   `is_heater_enabled(data: dict | None) -> bool | None`: Derives heater armed state.
    *   `get_jets_state(data: dict, jet_id: str) -> str`: Returns state (`"off"`, `"on"`) for target jets (`jets_left` or `jets_right`).
    *   `build_light_command(on: bool, color: str | None = None) -> bytes`: Overrides to build a **custom 16-byte payload** directly: `01 30 10 3C A1 00 A1 00 00 40 40 02 04 00 00 [0x80+idx]`.
    *   `build_jets_command(jet_id: str, target: str) -> bytes | None`: Matches P23 logic for independent pumps (`jets_left`, `jets_right`).
    *   `build_heater_command(on: bool) -> bytes`: Matches P23 logic (`btn_group=0x08`, `btn_action=0x18` if on else `0x11`).
    *   `build_blower_command(on: bool) -> bytes`: Matches P23 logic (`btn_group=0x04`, `btn_action=0x04` if on else `0x00`).
    *   `build_temp_command(target_celsius: int) -> bytes | None`: Matches P23 logic (`btn_group=0x80`, `btn_action=0x80`, using mapped setpoint in Fahrenheit).
    *   `build_ozone_manual_command(on: bool, setpoint_f: int = 0x62) -> bytes`: Overrides to build custom P20 command utilizing `jet_b7=0x80`, `jet_b8=0x80` (ON) or `jet_b7=0x80`, `jet_b8=0x00` (OFF).
    *   `build_ozone_mode_command(...)` and `build_heater_mode_command(...)`: Returns `b""` (unsupported).
    *   `build_schedule_command(...)`: Matches P23 schedule construction.
    *   `build_datetime_command(...)`, `build_time_command(...)`, and `build_date_command(...)`: Matches P23 time-setting layout.

*   Defines `P20B29Adapter` subclass.
*   Uses logical signature byte `0x01` at logical offset 8 (`1A FF 01 3C D2 B4 FF 08 01`).
*   Sets `has_blower = True`.
*   Configures dual single-speed pumps (`jets_left`, `jets_right`).
*   Configures `supported_light_colors`: `["auto", "red", "green", "yellow", "blue", "purple", "cyan", "white"]` (aligned with the P25B37).
*   Defines a custom `heater_state_map` to support the `0x20` base offset:
    ```python
    heater_state_map = {
        0x20: "off",  # or "standby"
        0x21: "circulation",
        0x24: "heating",
        0x25: "heating",
    }
    ```
    **Note:** These state values are ✨ derived (extrapolated from the P25B37 pattern offset by `+0x20`). Only `0x20` (idle) and `0x28` (blower ON) have been verified in captures. Heater-active and ozone-active captures from physical P20B29 hardware are still needed.

### Manual Mode Entities on P20B29

> [!WARNING]
> **Byte 13 anomaly:** All captured P20B29 broadcast frames show byte 13 as constant `0x6F` (`01101111`). The P23/P25 bit-flag pattern (bit `0x80` = ozone manual, bit `0x10` = heater manual) does not reliably decode from this value. We cannot determine whether the P20B29 encodes these flags differently or doesn't support mode switching at all.

**Decision: Omit Manual Mode config switches; bypass availability lock for Ozone/Heater switches.**

The P20B29 entity descriptions will:
1. **Exclude** `SpaManualOzoneSwitch` and `SpaManualHeaterSwitch` from the entity list.
2. **Make `SpaOzoneSwitch` and `SpaHeaterSwitch` always available** — the P20B29 adapter overrides `parse_status` to set `ozone_mode` and `heater_mode` to `None`, and the switch platform's `available` property will be updated to treat `None` mode as "always available" (i.e., bypass the manual-mode gate when the adapter doesn't support mode detection).
3. The direct ozone command (`jet_b7=0x80`) is ✅ verified and works regardless of mode state.
4. The direct heater command is ✨ derived (same bytes as P23) and expected to work.

This approach keeps the P20B29 functional while documenting the limitation. If future captures reveal the byte 13 encoding, mode switches can be re-enabled.

#### [MODIFY] [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py)
- Update `SpaOzoneSwitch.available` to return `True` when `coordinator.ozone_mode` is `None` (i.e., bypass the manual-mode gate when the adapter doesn't report mode state):
  ```python
  @property
  def available(self) -> bool:
      mode = self.coordinator.ozone_mode
      if mode is None:
          return super().available  # no mode detection → always available
      return super().available and mode == OZONE_MODE_MANUAL
  ```
- Apply the same pattern to `SpaHeaterSwitch.available` for `coordinator.heater_mode`.

#### [MODIFY] [adapters init](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py)
Register the new model:
- Import `P20B29Adapter` from `.p20`.
- Add `"P20B29": P20B29Adapter` to `ADAPTERS`.

### Test Suite

#### [NEW] [test_p20b29_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p20b29_adapter.py)
Create unit tests for the P20B29 adapter:
- Test status parsing with Yannick's unescaped idle (`0x20` base) and active broadcast captures.
- Test that `ozone_mode` / `heater_mode` are returned as `None`.
- Test command building (`build_light_command` with colors, `build_jets_command`, `build_ozone_manual_command`, etc.).
- Test that `build_ozone_mode_command` / `build_heater_mode_command` return `b""`.
- Test light command produces correct 16-byte payload (not 17-byte).

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
