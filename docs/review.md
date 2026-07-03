# Code Review: ha-joyonway Integration

**Scope**: Full integration review with focus on the P20(B29) adapter and model selection.
**Baseline**: All 209 tests pass, ruff clean, mypy clean.

## Findings by Severity

### CRITICAL — Logic Flaws

#### C1. P20B29 `ozone_mode=None` breaks the Ozone switch availability gate

**File**: [p20.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L203)
**Symptom**: The P20B29 adapter always returns `"ozone_mode": None` and `"heater_mode": None` in `parse_status()` (lines 203-204), because mode switching has not been verified on this model.

**Impact**: In [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L264-L270), the `SpaOzoneSwitch.available` property reads `self.coordinator.ozone_mode` which hits [coordinator.py L383](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L383):

```python
def ozone_mode(self) -> str | None:
    if self.data is None:
        return OZONE_MODE_AUTO
    return self.data.get("ozone_mode", OZONE_MODE_AUTO)
```

When `data["ozone_mode"]` is explicitly `None`, `dict.get()` returns `None` (because the key exists — the default is only used for *missing* keys). The switch availability check then executes:

```python
mode = self.coordinator.ozone_mode  # returns None
if mode is None:
    return super().available      # always available
```

This makes the Ozone switch always available on P20B29. **This is correct by accident** — the `mode is None` branch was intended for "this model doesn't support modes" — but it relies on a subtle distinction between `None` value vs missing key in `dict.get()`. If the P20 adapter were refactored to omit the key entirely, this would silently change behavior.

The same pattern applies to `heater_mode` and the `SpaHeaterSwitch` availability gate.

**Fix**: Make the intent explicit. In the coordinator's `ozone_mode`/`heater_mode` properties, treat an explicit `None` value the same as a missing key:

```python
@property
def ozone_mode(self) -> str | None:
    if self.data is None:
        return OZONE_MODE_AUTO
    mode = self.data.get("ozone_mode")
    return mode if mode is not None else None
```

Or better: have the P20 adapter not emit the keys at all, and update the coordinator to return `None` for missing keys (not `OZONE_MODE_AUTO`).

#### C2. P20B29 `heater_enabled` uses a completely different derivation than P23/P25

**File**: [p20.py L199](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L199)

P25 and P23 derive `heater_enabled` via `bool(heater_byte & 0x10)` — checking bit 4 of the heater status byte. P20 uses `heater_base in (0x21, 0x24, 0x25)` — a hardcoded value set.

The P20 heater state map maps `{0x20: "off", 0x21: "circulation", 0x24: "heating", 0x25: "heating"}`. Since `0x21 & 0x10 == 0x00`, the P23/P25 bitmask approach would yield `heater_enabled = False` for circulation state `0x21` on P20. So the P20 code uses an explicit value list instead.

This is *intentionally different* but is not documented, making it fragile. If a new heater byte is discovered (e.g., an ozone state `0x41`), the value list must be updated in sync with the heater state map.

**Fix**: Add a comment explaining the divergence. Or better: derive `heater_enabled` from the already-computed `status` field:

```python
"heater_enabled": status in ("circulation", "heating", "standby"),
```

This would be consistent across all adapters without hardcoding byte values twice.

#### C3. `climate.py` imports `TEMP_MIN_C` / `TEMP_MAX_C` from `adapters.p25` only

**File**: [climate.py L28](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/climate.py#L28)

```python
from .adapters.p25 import TEMP_MAX_C, TEMP_MIN_C
```

All three adapter modules define their own `TEMP_MIN_C = 10` and `TEMP_MAX_C = 40`, but the climate entity hardcodes the import from `p25`. If a future adapter defines a different range (e.g., a model that only goes to 38°C), the climate entity would still use the P25 range.

**Fix**: Move `TEMP_MIN_C` / `TEMP_MAX_C` to `const.py` as the canonical source, or better: expose them as adapter properties (e.g., `adapter.temp_min_c`, `adapter.temp_max_c`) and have the climate entity read from the adapter.

#### C4. `light.py` imports `LIGHT_COLOR_INDEX_TO_NAME` from `adapters.p25` even for P20 adapter

**File**: [light.py L167-L178](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/light.py#L167-L178)

The light entity's `effect` property does:

```python
from .adapters.p25 import LIGHT_COLOR_INDEX_TO_NAME
return LIGHT_COLOR_INDEX_TO_NAME.get(self._pending_color_index)
```

The P20 adapter defines its own `LIGHT_COLOR_INDEX_TO_NAME` in `p20.py` (L124-133) with the same values — but the light entity always uses the P25 copy. Both happen to be identical right now, but this is a latent coupling. If P20 adds a model-specific color (e.g., index 9 = "warm_white"), the light entity would not see it.

**Fix**: Add a method to the `ModelAdapter` protocol, e.g., `color_index_to_name(index: int) -> str | None`, and call it from the light entity.

### HIGH — Inconsistencies

#### H1. P20B29 `unescape_full_frame = True` while P23B32 `unescape_full_frame = False` — correctness not documented

**File**: [p20.py L154](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L154) vs [p23.py L158](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py#L158)

The P20 adapter uses full-frame unescape (like P25), while P23 uses tail-only unescape. The P23 module's docstring says "Tail-only (full payload unescape corrupts data)." but the P20 module has no such documentation. Was the P20 behavior verified on real hardware? If the P20 shares the same controller lineage as P23, full unescape might silently corrupt datetime bytes.

**Fix**: Document the unescape choice rationale in the P20 adapter docstring.

#### H2. P20B29 light mask (`0x0F`) vs P23B32 light mask (`0x01`) inconsistency

**File**: [p20.py L75](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L75) vs [p23.py L75](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py#L75)

P20 uses `MASK_LIGHT = 0x0F` (lower 4 bits, for color index support), matching P25. P23 uses `MASK_LIGHT = 0x01` (single bit, on/off only). The P20 adapter consequently emits `"light_color_index"` in its status dict while P23 does not.

This seems intentional (P20 has a color touchpad), but creates a data shape mismatch: `sensor.py` and `light.py` entities blindly read `"light_color_index"` from coordinator data. For P23, this key is absent, meaning `light.effect` returns `None` — which is correct for a model without color support. But the inconsistency is undocumented.

**Fix**: Document why P20 exposes `light_color_index` and P23 doesn't. Consider adding `light_color_index: 0` to P23's status for data shape consistency.

#### H3. P20B29 parse_status has a flexible signature check but `broadcast_signature` is the strict version

**File**: [p20.py L169-L174](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L169-L174)

The `parse_status()` method checks `frame[7] not in (0x06, 0x08)`, allowing two variants at index 7. But the class attribute `broadcast_signature` (L24, L564) is set to `P20B29_SIGNATURE` which hardcodes `0x08` at index 7.

The `broadcast_signature` attribute is part of the `ModelAdapter` protocol. If any code (e.g., auto-detection) uses `broadcast_signature` for matching, it would miss `0x06` frames. Currently the config flow's `_detect_model()` checks `raw_frame[8]` (index 8 → the model discriminator `0x01`) so it's not broken, but the mismatch is misleading.

**Fix**: Either update `broadcast_signature` to only contain the common prefix (bytes 0-6), or document that `parse_status()` intentionally accepts a wider set than `broadcast_signature`.

#### H4. `SpaOzoneSwitch` is always created for P20B29 even though ozone mode switching returns `b""`

**File**: [switch.py L44-L50](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L44-L50)

The entity setup conditionally excludes `SpaManualOzoneSwitch` and `SpaManualHeaterSwitch` for P20B29:
```python
if coordinator.adapter.model != "P20B29":
    entities.extend([SpaManualOzoneSwitch(...), SpaManualHeaterSwitch(...)])
```

But `SpaOzoneSwitch` (the manual ON/OFF ozone switch) is *always* created. Its `_submit_ozone_intent` calls `adapter.build_ozone_manual_command()`, which on P20 uses the `_build_button_command` approach with `jet_b7=0x80`. This is fine.

However, the availability gate check (`ozone_mode is None → always available`) means the ozone ON/OFF switch appears available even when the spa is in auto ozone mode. On P23/P25, the ozone switch is only available when `ozone_mode == "manual"`. On P20, since mode detection isn't supported, it's always available — which is the intended design (per the AGENTS.md constraint). But this should be explicit.

**Fix**: Add a `supports_ozone_mode` property to the adapter protocol, and use it in the switch entity setup and availability logic, rather than branching on `model != "P20B29"` string comparison.

#### H5. Model exclusion by string comparison (`model != "P20B29"`) is fragile

**File**: [switch.py L44](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L44)

This pattern hardcodes a model string. If a second model also lacks mode support (e.g., a future "P18B22"), this check must be updated. The adapter pattern exists to abstract model differences — use it.

**Fix**: Add a boolean attribute like `supports_mode_switching: bool` to the `ModelAdapter` protocol and base adapters. Then:
```python
if coordinator.adapter.supports_mode_switching:
    entities.extend([SpaManualOzoneSwitch(...), SpaManualHeaterSwitch(...)])
```

### MEDIUM — Python / HA Best Practices

#### M1. `ADAPTERS` registry uses `dict[str, type]` instead of `dict[str, type[ModelAdapter]]`

**File**: [adapters/__init__.py L11](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/__init__.py#L11)

```python
ADAPTERS: dict[str, type] = { ... }
```

This loses type information. Should be `dict[str, type[ModelAdapter]]` — but since `ModelAdapter` is a `Protocol`, this won't directly work with `type[Protocol]` in mypy. At minimum, the return type annotation on `get_adapter()` is correct. Consider a `TypeAlias` or comment.

#### M2. `SIGNATURE_MODEL_MAP` in config_flow is missing P20B29 detection validation

**File**: [config_flow.py L41-L45](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L41-L45)

```python
SIGNATURE_MODEL_MAP: dict[int, str] = {
    0x01: "P20B29",
    0x02: "P23B32",
    0x03: "P25B85",
}
```

The P20B29 entry maps signature byte `0x01` → `"P20B29"`. But on the P25 family there are *two* models (P25B85 and P25B37) sharing signature byte `0x03`, and only P25B85 is in this map. If a user has P25B37, auto-detection would suggest P25B85.

Also, `P20B29_SIGNATURE` has `0x01` at index 8, which does match this table. But the config_flow detection checks `raw_frame[8]` which is the *raw* (wire) frame byte. If escape encoding could affect index 8, the detection would be wrong. In practice, `0x01` is not an escapable byte, so it's fine — but the assumption is undocumented.

**Fix**: Add a comment noting that P25B37 shares signature byte `0x03` with P25B85 and is not separately auto-detectable.

#### M3. Massive code duplication across P20, P23, and P25 adapters

All three adapter modules (`p20.py`, `p23.py`, `p25.py`) contain near-identical implementations of:
- `_fahrenheit_to_celsius()` / `_celsius_to_fahrenheit()` (identical in all three)
- `_MAPPED_INDEXES` set (identical in all three)
- Schedule parsing logic in `parse_status()` (identical copy-paste)
- Entity description lists (identical structure, minor key differences)
- `build_schedule_command()`, `build_datetime_command()`, `build_time_command()`, `build_date_command()` (identical between P20 and P23, differ only in command prefix byte for P25)
- `is_heater_enabled()` (identical in all three)
- `get_jets_state()` (differs only for P25's "jets" vs "jets_left"/"jets_right")
- Unmapped bytes hash computation (identical in all three)

**Fix**: Extract shared utilities:
1. Move `_fahrenheit_to_celsius()` / `_celsius_to_fahrenheit()` to `base.py` or `protocol.py`
2. Create a `BaseAdapter` class in `base.py` with shared `parse_status()` template, `is_heater_enabled()`, `build_schedule_command()`, `build_datetime_command()` etc.
3. Have P20, P23, P25 inherit from the shared base class, overriding only what differs (signature check, command prefixes, heater state map)

This would reduce each adapter from ~700 lines to ~50-100 lines of model-specific code.

#### M4. `_PendingGroup` mutable default for `on_failure_callbacks` in `dataclass`

**File**: [coordinator.py L71](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L71)

```python
@dataclass
class _PendingGroup:
    on_failure_callbacks: list[Callable[[], None]] = field(default_factory=list)
```

This is correct (uses `field(default_factory=list)`). No issue here — included for completeness as it was reviewed.

#### M5. `JoyonwayConfigEntry` type alias is defined at the bottom of `coordinator.py`

**File**: [coordinator.py L760](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L760)

```python
type JoyonwayConfigEntry = ConfigEntry[JoyonwayCoordinator]
```

This uses the Python 3.12+ `type` statement. While it works, defining it at the bottom of the file where the coordinator class is implemented means every other module (`switch.py`, `climate.py`, `fan.py`, etc.) must import from `coordinator.py`. This creates a mild coupling concern — these modules need coordinator just for the type alias, not for any runtime logic.

**Fix**: Move `JoyonwayConfigEntry` to `const.py` with a forward reference, or to a separate `types.py` file, to break the circular dependency risk. (Currently not a runtime issue due to `TYPE_CHECKING` not being used for this import.)

#### M6. `entity.py` `device_info()` uses `configuration_url` pointing to the bridge HTTP interface

**File**: [entity.py L26](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/entity.py#L26)

```python
configuration_url=f"http://{entry.data[CONF_HOST]}",
```

The RS485 bridge (Elfin EW11) may or may not have an HTTP interface on port 80. If it does, this is useful. If not, this creates a dead link in the HA device page. Most EW11 bridges do have a web config UI, so this is probably fine, but worth noting.

#### M7. `build_ozone_mode_command()` and `build_heater_mode_command()` return `b""` instead of raising

**File**: [p20.py L421-L425](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L421-L425), [p23.py L402-L406](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py#L402-L406)

Both P20 and P23 adapters return `b""` for unsupported mode commands. The caller in `switch.py` (L543, L605) checks `if not cmd:` and raises `IntentBuildError`. This works because `b""` is falsy.

But `b""` is a valid `bytes` value that could be sent on the wire if the check is missed. Returning `None` or raising `NotImplementedError` would be safer and more explicit.

**Fix**: Return `None` (matching the return type pattern of `build_jets_command` and `build_temp_command` for unsupported operations) or raise `NotImplementedError`.

#### M8. `coordinator.py` imports `OZONE_MODE_AUTO` but never uses it for the coordinator's default

**File**: [coordinator.py L383](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L383)

The `ozone_mode` property returns `OZONE_MODE_AUTO` as the default, but `heater_mode` returns the string literal `"auto"`:

```python
@property
def ozone_mode(self) -> str | None:
    return self.data.get("ozone_mode", OZONE_MODE_AUTO)

@property
def heater_mode(self) -> str | None:
    return self.data.get("heater_mode", "auto")  # ← string literal
```

Both should use constants.

**Fix**: Add `HEATER_MODE_AUTO = "auto"` to `const.py` and use it in `heater_mode`.

### LOW — Cosmetic / Maintenance

#### L1. P23B32 `build_light_command()` raises `NotImplementedError` in the base class

**File**: [p23.py L357-L358](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23.py#L357-L358)

```python
def build_light_command(self, on: bool, color: str | None = None) -> bytes:
    raise NotImplementedError
```

This is in `P23BaseAdapter`, not the concrete `P23B32Adapter`. If someone creates a new P23 variant that forgets to override this, they get a clear error. Good pattern — just noting the inconsistency with P20 which has the method directly on `P20BaseAdapter`.

#### L2. `_MAPPED_INDEXES` sets are not sorted and have gaps

**Files**: All three adapter modules

The index sets are written in an ad-hoc order (e.g., `{0, 1, 2, ..., 9, 12, 13, 14, 16, 17, 28, 19, 20, ...}` — note `28` appearing before `19`). This makes it hard to verify correctness visually.

**Fix**: Sort the sets or generate them from the `IDX_*` constants.

#### L3. Duplicated schedule data validation in `switch.py` and `time.py`

**Files**: [switch.py L360-L380](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L360-L380), [time.py L236-L257](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/time.py#L236-L257)

Both `SpaScheduleSlotSwitch._validate_schedule_data_available()` and `SpaScheduleTime._validate_schedule_data_available()` contain identical validation logic. And both `_build_schedule_state()` and `_build_schedule_time()` re-check the same keys at drain time.

**Fix**: Extract to a shared helper in `entity.py` or a new `schedule.py` utility module.

#### L4. `sensor.py` hardcodes raw byte sensor keys

**File**: [sensor.py L102-L108](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/sensor.py#L102-L108)

```python
if value is not None and self._key in {
    "heater_byte_raw",
    "jets_byte_raw",
    "ozone_mode_byte_raw",
    "activity_byte_raw",
    "light_cycle_byte_raw",
}:
    return f"0x{value:02X}"
```

This is a set of magic strings that must be kept in sync with entity descriptions across all adapters.

**Fix**: Add a `format_hex: bool = False` field to `SpaEntityDescription` and use it in the sensor.

#### L5. `P20BaseAdapter.supported_light_colors` initialized as empty list, overridden in `P20B29Adapter`

**File**: [p20.py L157](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p20.py#L157)

The base class has `supported_light_colors: list[str] = []` and the concrete class overrides it with the full color list. This is fine functionally but means someone inheriting from `P20BaseAdapter` for a future P20 variant gets no color support by default — which is safer.

No action needed.

## Summary

| Severity | Count | Key Theme |
|----------|-------|-----------|
| Critical | 4 | Cross-adapter semantic mismatches (ozone mode None, heater derivation, hardcoded imports from p25) |
| High | 5 | Adapter inconsistencies (unescape policy, light mask, signature vs parse, model string branching) |
| Medium | 8 | HA/Python best practices (type annotations, constant usage, code duplication, type alias placement) |
| Low | 5 | Cosmetic / maintenance (unsorted sets, duplicated validation, magic strings) |

The integration is well-structured overall with solid safety patterns (intent queue, optimistic state, grace-mode availability). The main risks are around the P20B29 adapter's implicit reliance on `None` values to bypass availability gates designed for models with mode switching, and the hardcoded cross-adapter imports in `climate.py` and `light.py` that silently assume all models share the same constants.
