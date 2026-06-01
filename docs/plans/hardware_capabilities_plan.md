# Priority 4: Hardware Capability Options — Implementation Plan

> **Goal:** Add a "Hardware" section in the options flow where users declare
> which optional hardware is physically present on their spa build. Entities
> for absent hardware are not created at all (instead of being created but
> disabled by default), giving a cleaner UI and avoiding confusion.

## 1. Scope

### Current state
- The **blower switch** is always created with
  `_attr_entity_registry_enabled_default = False`. Users who don't have a
  blower see a disabled entity in the entity registry that they must know to
  ignore.
- The **ozone switch** already uses conditional creation (only created when
  ozone mode = Manual), which is the pattern we want to generalize.

### Target state
- A new "Hardware" section in the options flow with a boolean toggle:
  **"Blower installed"** (default: `False`).
- When `blower_installed = False`: the blower switch entity is **not created**
  and any existing blower entity registry entry is removed.
- When `blower_installed = True`: the blower switch entity is created and
  **enabled by default** (no more disabled-by-default workaround).
- Changing the option triggers a config-entry reload so entities are
  re-created with the new set.
- The pattern is extensible for future optional hardware (e.g., a second pump,
  UV lamp, aux output).

## 2. Changes by File

### 2.1 `const.py` — new option key

```python
# Options keys (existing)
OPT_OZONE_MODE: str = "ozone_mode"
OPT_AUTO_SYNC_CLOCK: str = "auto_sync_clock"

# New
OPT_BLOWER_INSTALLED: str = "blower_installed"
```

### 2.2 `config_flow.py` — options flow UI

Add the blower toggle to `async_step_init`. Group it visually under a
"Hardware" concept using the schema ordering (HA shows fields in schema order).

```python
current_blower = self.options.get(OPT_BLOWER_INSTALLED, False)

schema = vol.Schema(
    {
        # Existing fields
        vol.Required(OPT_OZONE_MODE, default=current_ozone_mode): vol.In({...}),
        vol.Required(OPT_AUTO_SYNC_CLOCK, default=current_auto_sync): bool,
        # Hardware section
        vol.Required(OPT_BLOWER_INSTALLED, default=current_blower): bool,
    }
)
```

Add translation strings for the new field (see §2.5).

### 2.3 `switch.py` — conditional blower creation

Replace the unconditional `SpaBlowerSwitch(...)` with a conditional check,
mirroring the existing ozone pattern:

```python
async def async_setup_entry(...) -> None:
    coordinator: JoyonwayP25B85Coordinator = hass.data[DOMAIN][entry.entry_id]
    blower_installed = entry.options.get(OPT_BLOWER_INSTALLED, False)

    entities: list[SwitchEntity] = [
        SpaHeaterSwitch(coordinator, entry),
        SpaLightSwitch(coordinator, entry),
        *([SpaOzoneSwitch(coordinator, entry)]
          if coordinator.ozone_mode == OZONE_MODE_MANUAL else []),
        *([SpaBlowerSwitch(coordinator, entry)]
          if blower_installed else []),
        SpaScheduleSlotSwitch(coordinator, entry, "heat", 1),
        # ...
    ]
```

In `SpaBlowerSwitch`, **remove** `_attr_entity_registry_enabled_default = False`
so that when the entity is created it is enabled by default.

### 2.4 `__init__.py` — add registry cleanup for removed hardware entities

The existing `_async_options_updated` reload path is still correct, but we
also need explicit entity-registry cleanup when optional hardware is disabled.
Without this, Home Assistant can keep a stale disabled registry row.

This cleanup runs only as part of an explicit options save action by the user.
Do not add startup/background cleanup.

Add a helper in `__init__.py`:

```python
from homeassistant.helpers import entity_registry as er
from homeassistant.core import callback

from .const import OPT_BLOWER_INSTALLED


@callback
def _remove_optional_entities_for_disabled_hardware(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    ent_reg = er.async_get(hass)
    blower_installed = entry.options.get(OPT_BLOWER_INSTALLED, False)

    if not blower_installed:
        unique_id = f"{entry.entry_id}_blower_switch"
        if entity_id := ent_reg.async_get_entity_id("switch", DOMAIN, unique_id):
            ent_reg.async_remove(entity_id)
```

Call this helper in `_async_options_updated` before
`hass.config_entries.async_reload(entry.entry_id)`.

### 2.5 Translations — new option label

Add the `blower_installed` field label and description to all translation files
and `strings.json`.

**`strings.json`** (base):
```json
{
  "config": { ... },
  "options": {
    "step": {
      "init": {
        "data": {
          "ozone_mode": "Ozone mode",
          "auto_sync_clock": "Automatically sync spa clock",
          "blower_installed": "Blower installed"
        },
        "data_description": {
          "blower_installed": "Enable if your spa has a physical air blower connected. When disabled, the blower switch entity is not created."
        }
      }
    }
  }
}
```

Repeat for `translations/en.json`, `de.json`, `fr.json` with localized strings:
- **de:** `"Gebläse installiert"` / `"Aktivieren, wenn Ihr Spa über ein physisches Luftgebläse verfügt."`
- **fr:** `"Souffleur installé"` / `"Activer si votre spa dispose d'un souffleur d'air physique."`

### 2.6 `adapters/p25b85.py` — no changes needed

The adapter still parses the `blower` key from broadcast frames regardless of
whether the entity exists. This is correct: the data is always available in
`coordinator.data` for diagnostics and for future use. Entity creation is a
UI/platform concern, not an adapter concern.

## 3. Test Plan

### 3.1 Unit tests (in `tests/`)

| Test | What it verifies |
|------|-----------------|
| `test_blower_switch_created_when_installed` | With `OPT_BLOWER_INSTALLED=True`, `SpaBlowerSwitch` is in the entity list |
| `test_blower_switch_omitted_when_not_installed` | With `OPT_BLOWER_INSTALLED=False` (or absent), no blower entity is created |
| `test_blower_enabled_by_default_when_created` | When created, `entity_registry_enabled_default` is `True` (not disabled) |
| `test_options_flow_shows_blower_field` | Options flow schema includes `blower_installed` |
| `test_options_change_triggers_reload` | Changing `blower_installed` triggers entry reload (existing reload path) |
| `test_blower_registry_entry_removed_when_disabled` | Existing blower registry entry is removed when `blower_installed=False` |

### 3.2 Manual / live test

1. Install integration with default options → verify no blower entity exists.
2. Open options → toggle "Blower installed" on → save → verify blower switch
   appears and is enabled.
3. Toggle blower on/off (if hardware present) → verify command works.
4. Open options → toggle "Blower installed" off → save → verify blower entity
   is removed from the entity registry (not only hidden/unavailable).

## 4. Migration / Backwards Compatibility

- This project currently has no existing installs, so defaulting
  `OPT_BLOWER_INSTALLED` to `False` is acceptable.
- Breaking changes are acceptable at this stage.
- No config entry version bump needed — the new option key simply defaults
  to `False` when absent from `entry.options`.

## 5. Future Extensibility

The same pattern can be reused for other optional hardware:

```python
OPT_AUX_OUTPUT_INSTALLED: str = "aux_output_installed"
OPT_SECOND_PUMP_INSTALLED: str = "second_pump_installed"
```

Each new capability adds one boolean to the options schema and one conditional
in the relevant platform's `async_setup_entry`. The adapter layer remains
unchanged — it always parses all known bytes.

## 6. Implementation Order

1. Add `OPT_BLOWER_INSTALLED` to `const.py`
2. Update `config_flow.py` options schema
3. Update `strings.json` + all translation files
4. Remove `_attr_entity_registry_enabled_default = False` from `SpaBlowerSwitch`
5. Add conditional creation in `switch.py` `async_setup_entry`
6. Add registry cleanup logic in `__init__.py` options-update path
7. Add/update unit tests
8. Update `README.md` with the new option and that breaking changes are
   acceptable in this pre-release phase
9. Update `docs/plan.md` to mark prio 4 as done

