# Implementation Plan: Model Confirmation Flow and Reconfigure Flow

This plan details the changes required to allow users to confirm their exact Joyonway controller model during initial setup (config flow) and update it later (reconfigure flow).

## Context & Objectives

Currently, the configuration flow automatically connects to the RS485 bridge and reads a broadcast frame to detect the controller model based on the signature byte at index 8 (`0x02` for P23B32, `0x03` for P25B85). **Problem**: on successful detection it immediately creates the config entry — the user never sees which model was detected, has no opportunity to correct it, and is unaware that a reconfigure option exists.

Additional issues with pure auto-detection:
1. The **P20B29** broadcasts `0x01` at index 8 (no adapter exists yet).
2. The **P25B37** broadcasts `0x03` at index 8 (colliding with the P25B85 signature), meaning it auto-detects as P25B85 but requires different write commands.
3. Unknown models (e.g. a hypothetical P23B42) would silently fall through to the default model, giving the user no indication that something is wrong.

To solve this, we introduce a two-step config flow (IP/Port → Model Confirmation dropdown) that **always** shows the model selection step, and a Reconfigure Flow to let users correct their choice later.

### Current File State (Reference)

**[config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py)**: Contains `_detect_model(host, port) -> str | None` which returns a model name string on success or `None` on connection failure. The `JoyonwayConfigFlow` class has a single step (`async_step_user`) that calls `_detect_model` and immediately creates the entry. No reconfigure or options flow exists.

**[adapters/\_\_init\_\_.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py)**: Contains the `ADAPTERS: dict[str, type]` registry mapping model names to adapter classes. Currently registered: `"P25B85"` → `P25B85Adapter`, `"P23B32"` → `P23B32Adapter`. The `get_adapter(model)` function raises `ValueError` for unknown models.

**[const.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/const.py)**: Defines `CONF_MODEL = "model"`, `DEFAULT_MODEL = "P25B85"`, `DEFAULT_HOST`, `DEFAULT_PORT`.

**[\_\_init\_\_.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/__init__.py)**: `async_setup_entry` reads `entry.data[CONF_MODEL]` and passes it to the coordinator. No update listener is registered.

**[translations/](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/translations/)**: Contains `en.json`, `de.json`, `fr.json`, `pl.json`. No `strings.json` exists (translations are the source files). The `config` section currently only has the `user` step, `error`, and `abort` keys. No `selector` or `reconfigure` keys exist.

## Design Decisions

* **Always Show Model Confirmation**:
  The model confirmation step is **always** displayed after a successful TCP connection — regardless of whether auto-detection succeeded. This serves three purposes: (1) the user sees and confirms what model was detected, (2) the user learns that a reconfigure option exists if something doesn't work later, and (3) users with unlisted models see the GitHub issue hint and know how to request support. The auto-detected model is pre-selected as a convenience, but the user must explicitly confirm it.

* **Dynamic Model Dropdown**:
  The dropdown options must be derived from the `ADAPTERS` registry in `adapters/__init__.py` rather than a hardcoded list. This way, adding a new adapter module automatically makes the model selectable in the UI without touching the config flow code.

* **Modern Select Selectors**:
  We will use Home Assistant's native `SelectSelector` (from `homeassistant.helpers.selector`) to construct the model dropdown form. This is the modern HA best practice, allowing the frontend to automatically handle option translations defined in translation files under the `selector` key.

* **Shared Form Schema Reuse**:
  Both the config flow model step (`async_step_model_confirm`) and the reconfigure flow step (`async_step_reconfigure`) present the same single-field dropdown model selector form. The implementation must use a shared helper function to construct this `vol.Schema` (which wraps the `SelectSelector`) to avoid code duplication.

* **Reconfigure Flow (not Options Flow)**:
  The `model` field is fundamental configuration data (it determines the entire adapter, entity set, and command builders). Per modern HA best practices, changing `data` post-setup must be done via a **reconfigure flow** (`async_step_reconfigure`), not an options flow. The reconfigure flow uses `self._get_reconfigure_entry()` to access the current entry and `self.async_update_reload_and_abort(data_updates=...)` to atomically update the entry data and trigger a reload — no manual update listener needed.

* **Graceful Unknown Signature Handling**:
  If the TCP connection succeeds but the broadcast signature byte does not match any known model (or maps to a model without an adapter, e.g. `0x01` → `"P20B29"`), the flow transitions to the model confirmation step with **no default pre-selected**. A `description_placeholders` mechanism injects a warning guiding the user to check their touchpad's **Menu > Set > About** screen. The step description always includes the GitHub issue link as the escape hatch for users whose model is not yet listed in the dropdown.

* **Signature Map vs. Adapter Registry**:
  The `SIGNATURE_MODEL_MAP` maps raw bytes to model name _hints_ — these are used solely for pre-selecting the dropdown default. The dropdown itself only lists models from `ADAPTERS`. If a signature maps to a model without an adapter (e.g. `0x01` → `"P20B29"`), the hint is silently discarded and no default is pre-selected, same as an unknown signature.

## Proposed Changes

### Auto-Detection and Config Flow

#### [MODIFY] [config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py)

1. **Import Updates**:
   Add imports for `SelectSelector`, `SelectSelectorConfig`, `SelectSelectorMode` from `homeassistant.helpers.selector`, and `ADAPTERS` from `.adapters`.

2. **Signature-to-Model Mapping**:
   Define a module-level constant mapping broadcast signature bytes to model name hints:
   ```python
   SIGNATURE_MODEL_MAP: dict[int, str] = {
       0x01: "P20B29",
       0x02: "P23B32",
       0x03: "P25B85",
   }
   ```
   Modify `_detect_model` to use this map. Return type changes to distinguish three states:
   * `str` (model name) — signature matched a known model
   * `""` (empty string) — connected successfully but signature unknown
   * `None` — connection failed

   The mapping lookup replaces the current inline `if/elif` chain at index 8.

3. **Shared Schema Helper**:
   Create a module-level helper function to build the model selector schema:
   ```python
   def _model_schema(default: str | None = None) -> vol.Schema:
       options = sorted(ADAPTERS.keys())
       selector = SelectSelector(
           SelectSelectorConfig(
               options=options,
               mode=SelectSelectorMode.DROPDOWN,
               translation_key="model",
           )
       )
       field = (
           vol.Required(CONF_MODEL, default=default)
           if default
           else vol.Required(CONF_MODEL)
       )
       return vol.Schema({field: selector})
   ```

4. **Transition Step**:
   In `async_step_user`, when `_detect_model` returns a non-`None` value (model name or empty string), do not call `async_create_entry`. Instead:
   * Store `host`, `port` in instance variables (`self._host`, `self._port`).
   * Resolve the detected model: set `self._detected_model` to the model name if it exists in `ADAPTERS`, otherwise `None`.
   * Transition to `return await self.async_step_model_confirm()`.
   * If `_detect_model` returns `None` (connection failed), show `cannot_connect` error as before.

5. **Model Confirmation Step** (`async_step_model_confirm`):
   * If `user_input is not None`: create the entry with `self._host`, `self._port`, and the selected `user_input[CONF_MODEL]`.
   * Otherwise show the form:
     * Build the schema via `_model_schema(default=self._detected_model)` — when `self._detected_model` is `None`, no default is pre-selected.
     * Use `description_placeholders` to inject a contextual `{auto_detect_note}`:
       * If model was auto-detected: `"✓ Auto-detected: **P25B85**. Confirm or change below."`
       * If signature unknown or model has no adapter: `"⚠️ Your controller model could not be automatically identified. Please select it manually."`

6. **Reconfigure Flow** (`async_step_reconfigure`):
   Implement directly on `JoyonwayConfigFlow`:
   * Access the current entry via `self._get_reconfigure_entry()`.
   * If `user_input is not None`:
     ```python
     return self.async_update_reload_and_abort(
         self._get_reconfigure_entry(),
         data_updates={CONF_MODEL: user_input[CONF_MODEL]},
     )
     ```
   * Otherwise show the form with `_model_schema(default=current_model)`.
   * No update listener, no separate handler class needed.

### Setup Module

#### [NO CHANGE] [\_\_init\_\_.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/__init__.py)

No changes needed. The reconfigure flow handles reload automatically via `async_update_reload_and_abort`. No update listener registration required.

### Localization and Translation Updates

#### [MODIFY] Translation files under [translations/](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/translations/)

Add the new `model_confirm` step, `reconfigure` step, `reconfigure_successful` abort reason, and `selector` block to all four translation files (`en.json`, `de.json`, `fr.json`, `pl.json`).

Example of the complete updated `config` and new `selector` sections for `en.json`:
```json
{
  "config": {
    "step": {
      "user": {
        "title": "Joyonway Spa",
        "description": "Enter the IP address and TCP port of your RS485 bridge.",
        "data": {
          "host": "IP address",
          "port": "TCP port"
        }
      },
      "model_confirm": {
        "title": "Confirm Spa Model",
        "description": "{auto_detect_note}\n\n*Hint: To find your model, navigate to **Menu** > **Set** > **About** on your touchpad screen, and scroll to the top or bottom of the page. See [our README](https://github.com/alexbde/ha-joyonway/blob/main/README.md) for more details.*\n\n*If your model is not listed, please [open a GitHub issue](https://github.com/alexbde/ha-joyonway/issues) to request support.*",
        "data": {
          "model": "Controller model"
        }
      },
      "reconfigure": {
        "title": "Reconfigure Spa Controller",
        "description": "Update the configured model of your Joyonway controller.\n\n*Hint: To find your model, navigate to **Menu** > **Set** > **About** on your touchpad screen, and scroll to the top or bottom of the page. See [our README](https://github.com/alexbde/ha-joyonway/blob/main/README.md) for more details.*\n\n*If your model is not listed, please [open a GitHub issue](https://github.com/alexbde/ha-joyonway/issues) to request support.*",
        "data": {
          "model": "Controller model"
        }
      }
    },
    "error": {
      "cannot_connect": "Cannot connect to the RS485 bridge. Check IP, port and that the bridge is powered on. Make sure no other client (phone app, other integration) is connected."
    },
    "abort": {
      "already_configured": "This bridge is already configured.",
      "reconfigure_successful": "Model updated successfully."
    }
  },
  "selector": {
    "model": {
      "options": {
        "P25B85": "Joyonway P25B85",
        "P23B32": "Joyonway P23B32"
      }
    }
  }
}
```

> **Note**: The `selector.model.options` keys must exactly match the `ADAPTERS` registry. Only models with existing adapters are listed. New entries are added here when a new adapter module lands (e.g. `"P25B37": "Joyonway P25B37"` once its adapter is merged).

## Verification Plan

### Automated Tests

Update [test_config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py):

* **`_detect_model` tests**:
  * Existing `test_detect_model_success`: Adjust to verify `0x03` → `"P25B85"` via `SIGNATURE_MODEL_MAP`.
  * Existing `test_detect_model_failure`: No change — returns `None` on `OSError`.
  * Existing `test_detect_model_empty_stream`: No change — returns `None` on empty read.
  * Add `test_detect_model_p23b32`: Verify `0x02` → `"P23B32"`.
  * Add `test_detect_model_unknown_signature`: Send a frame with `0x04` at index 8 → returns `""` (connected but unknown).

* **Config flow two-step tests**:
  * Update `test_config_flow_user_step_success`: Assert `result["type"] == "form"` and `result["step_id"] == "model_confirm"` (no longer creates entry directly). Verify `description_placeholders` contains the auto-detect note.
  * Add `test_config_flow_model_confirm_creates_entry`: Simulate submitting the `model_confirm` step with a model selection → assert `result["type"] == "create_entry"` with correct `data` dict.
  * Add `test_config_flow_model_confirm_unknown_signature`: Patch `_detect_model` to return `""` → assert `model_confirm` form has no default value and placeholder contains the warning text.
  * Existing `test_config_flow_user_step_cannot_connect`: No change — `_detect_model` returns `None`, error shown.

* **Reconfigure flow tests**:
  * Add `test_reconfigure_flow_shows_current_model`: Mock a config entry with `model="P23B32"`, call `async_step_reconfigure()` → assert form is shown with `default="P23B32"`.
  * Add `test_reconfigure_flow_updates_model`: Submit reconfigure with `model="P25B85"` → assert `async_update_reload_and_abort` is called with `data_updates={CONF_MODEL: "P25B85"}`.

### Lint and Type Checks

After implementation, run:
```bash
.venv/bin/ruff check custom_components/joyonway/config_flow.py
.venv/bin/ruff format custom_components/joyonway/config_flow.py
.venv/bin/mypy custom_components/joyonway/
.venv/bin/pytest -q -W ignore
```

## Next Step: Adapter Refactoring & P25B37 Support

> This section documents the architecture decisions for the **follow-up** adapter refactoring and P25B37 implementation that will land after the config flow changes above. Backwards compatibility for existing config entries is **not** a concern (single-user deployment).

### Adapter File Naming Convention

Adapter files are named by **model family prefix**, not by individual model number:

| File | Contains | Models |
|------|----------|--------|
| `adapters/p25.py` | `P25BaseAdapter`, `P25B85Adapter`, `P25B37Adapter` | P25B85, P25B37 |
| `adapters/p23.py` | `P23B32Adapter` (and future P23 variants) | P23B32 |
| `adapters/p20.py` | Future `P20B29Adapter` | P20B29 |

As part of this refactoring:
* Rename `p25b85.py` → `p25.py`
* Rename `p23b32.py` → `p23.py`

### Problem

The P25B37 shares broadcast signature `0x03` with the P25B85 and has an identical broadcast frame layout. The two models differ in only a few command-layer details (see [P25B37.md](file:///Users/alex/repositories/alexbde/ha-joyonway/scratch/P25B37.md)):

| Aspect | P25B85 | P25B37 |
|--------|--------|--------|
| Broadcast parsing (all offsets) | Standard | **Identical** |
| Command context byte (byte 12) | `0xC0` | `0x40` |
| Light command | Toggle (`btn_group=0x40, btn_action=0x40`, byte 15 = `0x00`) | Discrete ON=`0x81` / OFF=`0x80` at payload byte 15 |
| Status byte (offset 14) values | Known state map | **Possibly different** — reports `unknown` |
| Blower support | Yes | **Unconfirmed** — not reported on P25B37 hardware |
| Clock sync | Works | **No effect** — PB554 on P25B37 has no clock menu |
| Jets (dual-speed) | Same target bytes | **Identical** (works with changed context) |
| Heater, temp, ozone, schedules | Via `_build_button_command` | **Identical** (works with changed context) |

**~95% of the code is shared.** Duplicating the 846-line adapter file would be a maintenance liability.

### Recommended Approach: Base Class Extraction

Rename [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py) → `p25.py` and restructure into three classes in one file:

```
adapters/p25.py
├── P25BaseAdapter          ← All shared code (~95%): constants, parse_status,
│                             _build_button_command, jets, heater, temp,
│                             ozone, schedule, datetime, entity_descriptions
├── P25B85Adapter(P25Base)  ← model="P25B85", context=0xC0, toggle light
└── P25B37Adapter(P25Base)  ← model="P25B37", context=0x40, discrete light
```

#### P25BaseAdapter

Holds all shared constants, broadcast parsing, and command building. Key design points:

* **`_context_byte` class attribute + sentinel pattern**: Each subclass sets its default command context byte (`0xC0` for P25B85, `0x40` for P25B37). Since Python default argument values are bound at class definition time (you cannot write `context=self._context_byte`), the `_build_button_command` signature uses `context: int | None = None` and resolves the default at call time:
  ```python
  def _build_button_command(self, ..., context: int | None = None, ...) -> bytes:
      if context is None:
          context = self._context_byte
      ...
  ```
  This allows individual callers to still override context explicitly (e.g. `build_ozone_manual_command` always passes `context=0x40`).
* **`tail_byte` parameter**: Add a `tail_byte: int = 0x00` parameter for payload index 15 (currently hardcoded to `0x00`). P25B85 always passes `0x00`; P25B37's light command passes `0x81`/`0x80` at this position.
* **`build_light_command` is abstract**: Each subclass must implement it (toggle vs. discrete).
* **Ozone/heater mode commands**: `build_ozone_mode_command`, `build_heater_mode_command`, and `build_ozone_manual_command` always pass **explicit** context values (e.g. `0xC0`, `0x40`, `0x80`) — they do not rely on `_context_byte`. These commands should stay on the base class. Whether the same context values work on P25B37 is unverified; if they differ, the subclass can override the specific method.
* **All other command builders** (`build_jets_command`, `build_heater_command`, `build_blower_command`, `build_temp_command`, `build_schedule_command`, `build_datetime_command`) live on the base and work correctly for both models via the `_context_byte` sentinel.
* **`entity_descriptions`** returns the full P25 entity list. Subclasses can override to remove entities (e.g. P25B37 might exclude blower).

#### P25B85Adapter (~10 lines)

```python
class P25B85Adapter(P25BaseAdapter):
    model = "P25B85"
    _context_byte = 0xC0

    def build_light_command(self, on: bool) -> bytes:
        """P25B85 uses a toggle command; `on` is ignored."""
        return self._build_button_command(btn_group=0x40, btn_action=0x40)
```

#### P25B37Adapter (~15 lines)

```python
class P25B37Adapter(P25BaseAdapter):
    model = "P25B37"
    _context_byte = 0x40

    def build_light_command(self, on: bool) -> bytes:
        """P25B37 uses discrete ON/OFF via payload byte 15."""
        return self._build_button_command(
            btn_group=0x40, btn_action=0x40,
            tail_byte=0x81 if on else 0x80,
        )
```

### Registration

In [adapters/\_\_init\_\_.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py), update imports:

```python
from .p25 import P25B85Adapter, P25B37Adapter
from .p23 import P23B32Adapter

ADAPTERS: dict[str, type] = {
    "P25B85": P25B85Adapter,
    "P25B37": P25B37Adapter,
    "P23B32": P23B32Adapter,
}
```

### Translation Update

Add to the `selector.model.options` block in all four translation files:
```json
"P25B37": "Joyonway P25B37"
```

After registration, the config flow dropdown automatically includes P25B37 (dynamic from `ADAPTERS`).

### Test Impact

* Update all adapter imports:
  * `adapters.p25b85` → `adapters.p25`
  * `adapters.p23b32` → `adapters.p23`
* Add P25B37-specific tests verifying:
  * `_context_byte == 0x40`
  * `build_light_command(True)` produces payload with byte 15 = `0x81`
  * `build_light_command(False)` produces payload with byte 15 = `0x80`
  * `build_jets_command` produces payload with context byte 12 = `0x40`
