# Implementation Plan: Model Configuration Flow and Options Flow

This plan details the changes required to allow users to select their exact Joyonway controller model during initial setup (config flow) and update it later (options flow).

## Context & Objectives

Currently, the configuration flow automatically connects to the RS485 bridge and reads a broadcast frame to detect the controller model based on the signature byte at index 8 (`0x02` for P23B32, `0x03` for P25B85). 

However:
1. The **P20B29** broadcasts `0x01` at index 8.
2. The **P25B37** broadcasts `0x03` at index 8 (colliding with the P25B85 signature), meaning it auto-detects as P25B85 but requires different write commands.

To support multiple models sharing broadcast signatures, we need a two-step config flow (IP/Port selection -> Model Confirmation dropdown) and an Options Flow to let users modify their choice later.

## Design Decisions

* **Modern Select Selectors**:
  We will use Home Assistant's native `SelectSelector` (from `homeassistant.helpers.selector`) to construct the model dropdown form. This is the modern HA best practice, allowing the frontend to automatically handle option translations defined in translation files under the `selector` key.
* **Shared Form Schema Reuse**:
  Both the config flow model step (`async_step_model_confirm`) and the options flow step (`async_step_init`) present the same single-field dropdown model selector form. The implementation should use a shared helper function/generator to construct this `vol.Schema` (which wraps the `SelectSelector`) to avoid code duplication.
* **Unified Help Context**:
  We will display helper markdown text directly inside the Home Assistant UI during configuration. This description guides the user on where to physically look for their model name and directs them to file a GitHub issue if their hardware is not yet listed.

## Proposed Changes

### Configuration Flow Step Update

#### [MODIFY] [config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py)

1. **Auto-Detection Model Mapping**:
   Modify `_detect_model` to map the broadcast signature byte at index 8 to a default model selection:
   * `0x01` -> `"P20B29"`
   * `0x02` -> `"P23B32"`
   * `0x03` -> `"P25B85"`

2. **Transition Step**:
   In `async_step_user`, if a model is successfully auto-detected, do not call `async_create_entry` immediately. Instead:
   * Store `host` and `port` in instance variables.
   * Save the detected model in `self.detected_model`.
   * Transition to a new step: `return await self.async_step_model_confirm()`.

3. **Model Confirmation Step**:
   Implement `async_step_model_confirm`:
   * Present a dropdown step with a selection of supported models: `"P25B85"`, `"P25B37"`, `"P23B32"`, `"P20B29"`.
   * Pre-select the `self.detected_model` as the default selection.
   * Build the schema using `SelectSelector` configured with `SelectSelectorConfig(options=[...], mode=SelectSelectorMode.DROPDOWN, translation_key="model")`.
   * Upon submission, call `self.async_create_entry` with the selected model.

4. **Options Flow Registration**:
   * Implement `async_get_options_flow` on `JoyonwayConfigFlow` to return an instance of `JoyonwayOptionsFlowHandler`.

5. **Options Flow Handler**:
   * Implement `JoyonwayOptionsFlowHandler` inheriting from `OptionsFlow`.
   * In `async_step_init`, present the same `SelectSelector`-based schema, pre-selected with the config entry's current model.
   * On submit, use `self.hass.config_entries.async_update_entry(self.config_entry, data=new_data)` to update the config entry's `data` dictionary with the new model value.

### Setup and Reload Updates

#### [MODIFY] [__init__.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/__init__.py)

1. **Update Listener Registration**:
   In `async_setup_entry`, register an update listener to trigger a reload when options/data change:
   ```python
   entry.async_on_unload(entry.add_update_listener(update_listener))
   ```

2. **Update Listener Helper**:
   Define the async reload helper function at the module level:
   ```python
   async def update_listener(hass: HomeAssistant, entry: JoyonwayConfigEntry) -> None:
       """Handle options update."""
       await hass.config_entries.async_reload(entry.entry_id)
   ```

### Localization and Translation Updates

#### [MODIFY] Translation Files under [translations/](file:///Users/alex/repositories/alexbde/custom_components/joyonway/translations/)

Add the new step and selector strings to `en.json`, `de.json`, `fr.json`, and `pl.json` translation files. 

Example snippet to add to English `en.json`:
```json
  "config": {
    "step": {
      "user": { ... },
      "model_confirm": {
        "title": "Confirm Spa Model",
        "description": "Confirm or select your exact Joyonway controller model.\n\n*Hint: To find your model, navigate to **Menu** > **Set** > **About** on your touchpad screen, and scroll to the top or bottom of the page. See [our README](https://github.com/alexbde/ha-joyonway/blob/main/README.md) for more details.*\n\n*If your model is not listed, please [open a GitHub issue](https://github.com/alexbde/ha-joyonway/issues) to request support.*",
        "data": {
          "model": "Controller model"
        }
      }
    }
  },
  "options": {
    "step": {
      "init": {
        "title": "Configure Spa Controller",
        "description": "Update the configured model of your Joyonway controller.\n\n*Hint: To find your model, navigate to **Menu** > **Set** > **About** on your touchpad screen, and scroll to the top or bottom of the page. See [our README](https://github.com/alexbde/ha-joyonway/blob/main/README.md) for more details.*\n\n*If your model is not listed, please [open a GitHub issue](https://github.com/alexbde/ha-joyonway/issues) to request support.*",
        "data": {
          "model": "Controller model"
        }
      }
    }
  },
  "selector": {
    "model": {
      "options": {
        "P25B85": "Joyonway P25B85",
        "P25B37": "Joyonway P25B37",
        "P23B32": "Joyonway P23B32",
        "P20B29": "Joyonway P20B29"
      }
    }
  }
```

## Verification Plan

### Automated Tests
* Update [tests/test_config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py):
  * Test that the config flow successfully transitions to the model confirmation step.
  * Verify that a custom selected model from the dropdown is correctly saved to the config entry.
  * Verify that the Options Flow successfully presents the current model and updates it in `entry.data` on submission, triggers reload, and re-initializes with the updated coordinator adapter.
