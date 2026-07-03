# Code Review: ha-joyonway Integration

**Scope**: Full integration review with focus on the P20(B29) adapter and model selection.
**Baseline**: 209 tests pass, ruff clean, mypy clean.

## Codebase Overview

This is a Home Assistant custom integration for Joyonway spa controllers. It communicates over RS485 via a TCP bridge (Elfin EW11, TCP Server mode). The architecture uses a modular adapter pattern where each controller model implements a `ModelAdapter` protocol.

### Key Files

| File | Purpose |
|------|---------|
| `custom_components/joyonway/adapters/base.py` | `ModelAdapter` Protocol class and shared dataclasses (`JetDescription`, `SpaEntityDescription`) |
| `custom_components/joyonway/adapters/p20.py` | P20B29 adapter (~710 lines). `P20BaseAdapter` class + `P20B29Adapter` subclass |
| `custom_components/joyonway/adapters/p23.py` | P23B32 adapter (~685 lines). `P23BaseAdapter` class + `P23B32Adapter` subclass |
| `custom_components/joyonway/adapters/p25.py` | P25B85/P25B37 adapters (~780 lines). `P25BaseAdapter` + two subclasses |
| `custom_components/joyonway/adapters/__init__.py` | Adapter registry `ADAPTERS` dict and `get_adapter()` factory |
| `custom_components/joyonway/coordinator.py` | `JoyonwayCoordinator` (TCP reader loop, `IntentQueue`, clock sync). Also defines `JoyonwayConfigEntry` type alias at L760 |
| `custom_components/joyonway/config_flow.py` | Config flow with auto-detection via `SIGNATURE_MODEL_MAP` and model confirmation steps |
| `custom_components/joyonway/const.py` | Constants (`DOMAIN`, `CONF_MODEL`, timing values, `PLATFORMS` list) |
| `custom_components/joyonway/entity.py` | `JoyonwayCoordinatorEntity` base class and `device_info()` helper |
| `custom_components/joyonway/switch.py` | Switch entities (Heater, Ozone, Blower, ManualOzone, ManualHeater, ScheduleSlot, AutoClockSync) |
| `custom_components/joyonway/climate.py` | Climate entity (thermostat with debounced setpoint) |
| `custom_components/joyonway/fan.py` | Fan entities (dual-speed `SpaJetsFan`, single-speed `SpaSingleSpeedFan`) |
| `custom_components/joyonway/light.py` | Light entity with color preset effects |
| `custom_components/joyonway/sensor.py` | Sensor entities driven by adapter's `entity_descriptions()` |
| `custom_components/joyonway/binary_sensor.py` | Binary sensors + bridge connectivity sensor |
| `custom_components/joyonway/time.py` | Schedule time entities (slot start/end times) |
| `custom_components/joyonway/protocol.py` | Frame parsing, escape/unescape, CRC-32 computation, `build_frame()` |

### Model Differences

| Property | P20B29 | P23B32 | P25B85 / P25B37 |
|----------|--------|--------|-----------------|
| Signature byte (index 8) | `0x01` | `0x02` | `0x03` |
| Command prefix (byte 1) | `0x30` | `0x30` | `0x20` |
| Unescape policy | Full frame | Tail-only (bytes 55+) | Full frame |
| Jet type | Two single-speed | Two single-speed | One dual-speed |
| Light colors | 8 presets (auto, red, …) | On/off only | P25B85: on/off only; P25B37: 8 presets |
| Ozone/Heater mode | Not supported (`None`) | Supported (auto/manual) | Supported (auto/manual) |
| Blower | Yes | Yes | Detected via DIP switch bit |
| Heater state map | `{0x20: off, 0x21: circ, 0x24: heat, 0x25: heat}` | `{0x40: off, 0x50: standby, 0x51: circ, 0x55: heat, …}` | Same as P23 |

## Findings

### F1. `climate.py` imports `TEMP_MIN_C` / `TEMP_MAX_C` from `adapters.p25` only

**File**: `climate.py` line 28

```python
from .adapters.p25 import TEMP_MAX_C, TEMP_MIN_C
```

All three adapter modules define their own `TEMP_MIN_C = 10` and `TEMP_MAX_C = 40`, but the climate entity hardcodes the import from `p25`. If a future adapter defines a different range (e.g., a model capped at 38°C), the climate entity would still use the P25 range.

**Fix**: Add `temp_min_c` and `temp_max_c` as properties on the `ModelAdapter` protocol in `base.py`. Set them on each adapter's base class. Have the climate entity read `coordinator.adapter.temp_min_c` / `coordinator.adapter.temp_max_c` instead of importing from `p25`.

### F2. `light.py` imports `LIGHT_COLOR_INDEX_TO_NAME` / `LIGHT_COLOR_NAME_TO_INDEX` from `adapters.p25` even for P20

**File**: `light.py` lines 167, 176, 202

The light entity's `effect` property does:

```python
from .adapters.p25 import LIGHT_COLOR_INDEX_TO_NAME
return LIGHT_COLOR_INDEX_TO_NAME.get(self._pending_color_index)
```

And `async_turn_on()` does:

```python
from .adapters.p25 import LIGHT_COLOR_NAME_TO_INDEX
target_idx = LIGHT_COLOR_NAME_TO_INDEX[target_effect]
```

The P20 adapter defines its own identical copy of these dicts in `p20.py` (lines 124-136) — but the light entity always uses the P25 copy. If P20 adds a model-specific color index, the light entity would not see it.

**Fix**: Add two methods to the `ModelAdapter` protocol in `base.py`:

```python
def color_index_to_name(self, index: int) -> str | None: ...
def color_name_to_index(self, name: str) -> int | None: ...
```

Implement them on each base adapter using the adapter's own lookup dicts. Update `light.py` to call `coordinator.adapter.color_index_to_name(index)` and `coordinator.adapter.color_name_to_index(name)` instead of importing from `p25`.

### F3. P20B29 `ozone_mode=None` interacts subtly with the Ozone switch availability gate

**File**: `p20.py` line 203, `coordinator.py` line 379-383, `switch.py` lines 264-270

The P20B29 adapter returns `"ozone_mode": None` and `"heater_mode": None` in `parse_status()` because mode switching is unverified on this model.

The coordinator's `ozone_mode` property:

```python
def ozone_mode(self) -> str | None:
    if self.data is None:
        return OZONE_MODE_AUTO
    return self.data.get("ozone_mode", OZONE_MODE_AUTO)
```

When `data["ozone_mode"]` is explicitly `None`, `dict.get()` returns `None` (the key exists, so the default is not used). The `SpaOzoneSwitch.available` property then:

```python
mode = self.coordinator.ozone_mode  # → None
if mode is None:
    return super().available        # → always available
```

The current behavior is correct (P20 ozone switch should be always available since mode switching isn't supported), but it works by accident — a refactor removing the key would silently change behavior.

The same pattern applies to `heater_mode` and `SpaHeaterSwitch`.

**Fix**: The P20 adapter should not emit `"ozone_mode"` and `"heater_mode"` keys at all (delete lines 203-204 in `p20.py`). Update the coordinator properties to explicitly return `None` for missing keys instead of a default:

```python
@property
def ozone_mode(self) -> str | None:
    if self.data is None:
        return None
    return self.data.get("ozone_mode")  # missing key → None → switch always available

@property
def heater_mode(self) -> str | None:
    if self.data is None:
        return None
    return self.data.get("heater_mode")  # missing key → None → switch always available
```

This makes the "model doesn't support modes" path explicit and robust.

### F4. P20B29 `heater_enabled` uses a different derivation than P23/P25

**File**: `p20.py` line 199

P25 and P23 derive `heater_enabled` via `bool(heater_byte & 0x10)` — checking bit 4. The P20 adapter uses `heater_base in (0x21, 0x24, 0x25)` — a hardcoded value set derived from the P20-specific heater state map.

The P20 heater state map is `{0x20: "off", 0x21: "circulation", 0x24: "heating", 0x25: "heating"}`. Since `0x21 & 0x10 == 0x00`, the P23/P25 bitmask approach would incorrectly report `heater_enabled = False` during circulation on P20. So the different approach is intentionally correct — but fragile: if a new heater byte is discovered, the value list must be updated in sync with the state map.

**Fix**: Derive `heater_enabled` from the already-computed `status` field instead of byte values:

```python
"heater_enabled": status in ("standby", "circulation", "heating"),
```

This is semantically correct across all models and eliminates the dual-maintenance of heater bytes. Apply this pattern in all three adapters to make `heater_enabled` consistent.

### F5. Model exclusion by string comparison (`model != "P20B29"`) in switch entity setup

**File**: `switch.py` lines 44-50

```python
if coordinator.adapter.model != "P20B29":
    entities.extend([
        SpaManualOzoneSwitch(coordinator, entry),
        SpaManualHeaterSwitch(coordinator, entry),
    ])
```

This hardcodes a model string. If a second model also lacks mode support, this check must be updated. The adapter pattern exists to abstract model differences.

**Fix**: Add a `supports_mode_switching: bool` attribute to the `ModelAdapter` protocol in `base.py`. Set it to `False` on `P20BaseAdapter`, `True` on `P23BaseAdapter` and `P25BaseAdapter`. Update `switch.py`:

```python
if coordinator.adapter.supports_mode_switching:
    entities.extend([
        SpaManualOzoneSwitch(coordinator, entry),
        SpaManualHeaterSwitch(coordinator, entry),
    ])
```

### F6. `build_ozone_mode_command()` and `build_heater_mode_command()` return `b""` for unsupported models

**File**: `p20.py` lines 421-425, `p23.py` lines 402-406

Both P20 and P23 return `b""` for mode commands they don't support. The caller checks `if not cmd:` and raises `IntentBuildError`, which works because `b""` is falsy — but `b""` is a valid `bytes` value that could accidentally be sent on the wire if the check is missed.

**Fix**: With F5 implemented (`supports_mode_switching = False`), the `SpaManualOzoneSwitch` / `SpaManualHeaterSwitch` entities are never created for these models, so the methods are unreachable. Change the return to `raise NotImplementedError("Mode switching not supported on this model")` for defense-in-depth. This matches the pattern already used by `P23BaseAdapter.build_light_command()` (line 358).

### F7. `heater_mode` coordinator property uses string literal `"auto"` instead of constant

**File**: `coordinator.py` line 390

```python
@property
def heater_mode(self) -> str | None:
    if self.data is None:
        return "auto"  # ← string literal
    return self.data.get("heater_mode", "auto")
```

Meanwhile `ozone_mode` uses the constant `OZONE_MODE_AUTO`.

**Fix**: This is superseded by F3 (both properties should return `None` when data is `None` or key is missing). After applying F3, no default string is needed, and this inconsistency disappears.

### F8. `ADAPTERS` registry uses `dict[str, type]` instead of a typed dict

**File**: `adapters/__init__.py` line 11

```python
ADAPTERS: dict[str, type] = { ... }
```

This loses type information about the adapter classes.

**Fix**: Since `ModelAdapter` is a `Protocol`, `type[ModelAdapter]` won't work cleanly with mypy for concrete classes. Use a descriptive comment instead:

```python
ADAPTERS: dict[str, type] = {  # type → ModelAdapter-implementing classes
    ...
}
```

### F9. `SIGNATURE_MODEL_MAP` maps `0x03` to `P25B85` only, silently misidentifying P25B37

**File**: `config_flow.py` lines 41-45

```python
SIGNATURE_MODEL_MAP: dict[int, str] = {
    0x01: "P20B29",
    0x02: "P23B32",
    0x03: "P25B85",
}
```

P25B85 and P25B37 share signature byte `0x03`. A P25B37 user would get auto-detected as P25B85.

**Fix**: Add a code comment documenting the ambiguity and that users can correct it on the model confirmation step:

```python
SIGNATURE_MODEL_MAP: dict[int, str] = {
    0x01: "P20B29",
    0x02: "P23B32",
    # P25B85 and P25B37 share signature byte 0x03; user can correct on the
    # model confirmation step. P25B85 is the more common variant.
    0x03: "P25B85",
}
```

### F10. P20B29 `broadcast_signature` class attribute contradicts `parse_status()` flexibility

**File**: `p20.py` lines 24, 169-174, 564

The `P20B29_SIGNATURE` constant and `broadcast_signature` class attribute hardcode `0x08` at index 7:

```python
P20B29_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x01])
```

But `parse_status()` accepts both `0x06` and `0x08` at index 7:

```python
if frame[7] not in (0x06, 0x08):
    return None
```

If any code uses `broadcast_signature` for matching, it would miss `0x06` frames. Currently the config flow's `_detect_model()` checks `raw_frame[8]` (index 8) so it's not broken, but the mismatch is misleading.

**Fix**: Update `broadcast_signature` to contain only the invariant common prefix (bytes 0-6 + byte 8), and add a comment explaining the flexibility at index 7. Alternatively, shorten `broadcast_signature` to the first 7 bytes that are truly fixed, and document the per-model matching in `parse_status()`.

### F11. P20B29 `unescape_full_frame = True` is not documented or justified

**File**: `p20.py` line 154

P23 uses tail-only unescape with a docstring: "Tail-only (full payload unescape corrupts data)." P20 uses full-frame unescape (like P25) with no explanation. If P20 shares the controller lineage with P23, this might silently corrupt datetime bytes.

**Fix**: Add a comment in the P20 adapter docstring or above the attribute explaining the rationale (e.g., "Full-frame unescape verified on community captures" or "Shares protocol behavior with P25 family per Sergiu's analysis").

### F12. Massive code duplication across P20, P23, and P25 adapters

**Files**: All three adapter modules total ~2100 lines with ~80% identical code

Near-identical implementations across all three modules:
- `_fahrenheit_to_celsius()` / `_celsius_to_fahrenheit()` — identical
- `_MAPPED_INDEXES` set — identical
- Schedule parsing blocks in `parse_status()` — identical copy-paste (~40 lines each)
- Entity description lists — identical structure, differing only in a few keys
- `build_schedule_command()`, `build_datetime_command()`, `build_time_command()`, `build_date_command()` — identical between P20 and P23, differ only in prefix byte for P25
- `is_heater_enabled()` — identical
- Unmapped bytes hash computation — identical

**Fix**: Extract shared code into a common base class in `base.py`:

1. Move `_fahrenheit_to_celsius()` / `_celsius_to_fahrenheit()` to `base.py` as module-level functions.
2. Move `_MAPPED_INDEXES` to `base.py`.
3. Create a `JoyonwayBaseAdapter` class in `base.py` containing:
   - `is_heater_enabled()`
   - `get_jets_state()` (with a generic implementation, overridden by P25 for dual-speed)
   - Template `parse_status()` with hooks for model-specific signature checks and state map
   - `build_schedule_command()`, `build_datetime_command()`, `build_time_command()`, `build_date_command()` — parameterized by the command prefix byte (P20/P23 use `0x30`, P25 uses `0x20`)
   - Shared unmapped bytes hash computation
   - Common entity description generation
4. Reduce each per-model adapter to ~50-100 lines of truly model-specific code (signature, heater state map, command prefix, jet definitions, light command format).

This is a large refactor. Keep the `ModelAdapter` Protocol for typing; `JoyonwayBaseAdapter` should satisfy it via structural subtyping.

### F13. Duplicated schedule data validation in `switch.py` and `time.py`

**Files**: `switch.py` lines 360-380, `time.py` lines 236-257

Both `SpaScheduleSlotSwitch._validate_schedule_data_available()` and `SpaScheduleTime._validate_schedule_data_available()` contain identical validation logic (checking for six required keys per schedule type).

**Fix**: Extract to a shared helper function in `entity.py`:

```python
def validate_schedule_data(data: dict | None, schedule_type: str) -> None:
    """Raise HomeAssistantError if schedule prerequisite data is missing."""
    if data is None:
        raise HomeAssistantError("No data available from spa")
    prefix = schedule_type
    required = [
        f"{prefix}_slot1_start", f"{prefix}_slot1_end",
        f"{prefix}_slot2_start", f"{prefix}_slot2_end",
        f"{prefix}_slot1_enabled", f"{prefix}_slot2_enabled",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        raise HomeAssistantError(
            f"Cannot send schedule: missing data keys {missing}. "
            "Wait for the spa to report a full broadcast."
        )
```

Call from both `switch.py` and `time.py`.

### F14. `sensor.py` hardcodes raw byte sensor keys for hex formatting

**File**: `sensor.py` lines 102-108

```python
if value is not None and self._key in {
    "heater_byte_raw", "jets_byte_raw", "ozone_mode_byte_raw",
    "activity_byte_raw", "light_cycle_byte_raw",
}:
    return f"0x{value:02X}"
```

This set of magic strings must be kept in sync with entity descriptions across all adapters.

**Fix**: Add a `format_hex: bool = False` field to `SpaEntityDescription` in `base.py`. Set `format_hex=True` on each raw byte entity description. Update `sensor.py`:

```python
if self._format_hex and value is not None:
    return f"0x{value:02X}"
```

### F15. `_MAPPED_INDEXES` sets are unsorted with interleaved values

**Files**: All three adapter modules

```python
_MAPPED_INDEXES = {0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 12, 13, 14, 16, 17, 28, 19, 20, ...}
```

Note `28` appearing between `17` and `19`. Hard to verify correctness visually.

**Fix**: After F12 (shared base class), this set lives in one place. Generate it from the `IDX_*` constants:

```python
_MAPPED_INDEXES = {
    *range(9),  # header bytes 0-8
    IDX_CURRENT_TEMP, IDX_JET_BYTE, IDX_OZONE_MODE, IDX_HEATER_STATE,
    IDX_SETPOINT, IDX_LIGHT_CYCLE, IDX_ACTIVITY_FLAG,
    *range(IDX_HEAT_SLOT1_START_H, IDX_HEAT_SLOT2_END_M + 1),
    *range(IDX_FILTER_SLOT1_START_H, IDX_FILTER_SLOT2_END_M + 1),
    *range(IDX_DATETIME_START, IDX_DATETIME_START + 6),
}
```

## Summary Table

| ID | Severity | Area | Finding |
|----|----------|------|---------|
| F1 | Critical | `climate.py` | Hardcoded import of `TEMP_MIN_C`/`TEMP_MAX_C` from `adapters.p25` — breaks if model ranges differ |
| F2 | Critical | `light.py` | Hardcoded import of color dicts from `adapters.p25` — ignores P20's own color mappings |
| F3 | Critical | Coordinator / P20 | `ozone_mode=None` / `heater_mode=None` works by accident via `dict.get()` semantics |
| F4 | Critical | P20 adapter | `heater_enabled` uses hardcoded byte values instead of derived `status` field |
| F5 | High | `switch.py` | Model exclusion via string comparison `model != "P20B29"` instead of adapter capability flag |
| F6 | High | P20 / P23 | Mode commands return `b""` instead of raising for unsupported operations |
| F7 | Medium | Coordinator | `heater_mode` uses string literal `"auto"` — superseded by F3 fix |
| F8 | Medium | Adapter registry | `ADAPTERS` dict typed as `dict[str, type]` — add descriptive comment |
| F9 | Medium | Config flow | `SIGNATURE_MODEL_MAP` maps `0x03` → P25B85 only, ambiguous with P25B37 |
| F10 | Medium | P20 adapter | `broadcast_signature` hardcodes `0x08` but `parse_status()` also accepts `0x06` |
| F11 | Medium | P20 adapter | `unescape_full_frame = True` rationale undocumented |
| F12 | Medium | All adapters | ~1500 lines of duplicated code across P20, P23, P25 modules |
| F13 | Low | `switch.py` / `time.py` | Identical schedule data validation duplicated in two files |
| F14 | Low | `sensor.py` | Raw byte hex formatting uses hardcoded key set instead of entity description flag |
| F15 | Low | All adapters | `_MAPPED_INDEXES` sets are unsorted and error-prone |

## Verification Plan

After implementing fixes, verify:

1. **Unit tests**: `.venv/bin/pytest -q -W ignore` — all 209+ tests must pass
2. **Linting**: `.venv/bin/ruff check custom_components/joyonway/` — clean
3. **Type checking**: `.venv/bin/mypy custom_components/joyonway/` — clean
4. **Dry-run simulation**: `.venv/bin/python tests/live/test_spa_controls.py --non-interactive` — 64 tests must pass
