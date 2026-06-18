# Dynamic Blower Detection via Broadcast Byte 13

## Goal

Replace the hardcoded per-model `has_blower` flag with dynamic detection from the broadcast frame. The controller's DIP switch configuration is published in **Byte 13, Bit 3 (`0x08`)** of every broadcast frame (`1` = blower present, `0` = no blower). This allows a single adapter class to correctly expose or hide the blower entity based on live hardware state, rather than requiring separate model subclasses for blower vs. no-blower configurations.

## Verification Summary

Bit 3 of Byte 13 was verified against **6,683 broadcast frames** across three controller models:

| Source | Frames | Byte 13 Observed | Bit 3 | `has_blower` | Match |
| :--- | :--- | :--- | :--- | :--- | :--- |
| P25B85 (all captures) | 6,572 | `0x7D`, `0xFD`, `0x6D` | Always `1` | Yes | ✅ |
| P25B37 (capture) | 111 | `0xF5` | Always `0` | No | ✅ |
| P20B29 (documented) | — | `0x6F` (constant) | `1` | Yes | ✅ |

> [!IMPORTANT]
> The `KDY_RAW` test fixture in `test_p25b85_adapter.py` has byte 13 = `0xF5` (bit 3 = 0). This does not match any real P25B85 capture and must be corrected.

## Scope

**P25 family only.** P23 and P20 families continue using the static `has_blower` adapter default because:
- P23: No captures available to verify bit 3 semantics.
- P20: Byte 13 is documented as anomalous (`0x6F` constant); bit layout may differ.

The coordinator fallback ensures zero behavior change for P23/P20.

## Proposed Changes

### 1. P25 Adapter

#### [MODIFY] [p25.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25.py)

Add a constant and expose the flag in `parse_status()`:

```python
# New constant (near existing MASK_OZONE_MODE_MANUAL / MASK_HEATER_MODE_MANUAL)
MASK_BLOWER_CONFIG = 0x08  # bit 3 on byte 13 = blower hardware present (DIP switch)
```

In `parse_status()` result dict, add after `"heater_mode"`:

```python
"blower_present": bool(ozone_mode_byte & MASK_BLOWER_CONFIG),
```

### 2. Coordinator

#### [MODIFY] [coordinator.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py)

Add a `has_blower` property (alongside existing `ozone_mode`, `heater_mode`):

```python
@property
def has_blower(self) -> bool:
    """Return whether the spa has a blower, from broadcast or adapter default."""
    if self.data is not None and "blower_present" in self.data:
        return self.data["blower_present"]
    return self._adapter.has_blower
```

Fallback chain:
1. Live broadcast `blower_present` field (P25 only, set every ~500ms).
2. Static adapter class attribute (P23, P20, or if first refresh fails).

This is safe because `async_config_entry_first_refresh()` completes **before** `async_forward_entry_setups()` creates entities.

### 3. Switch Platform

#### [MODIFY] [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py)

Change the blower entity setup guard:

```diff
-            if coordinator.adapter.has_blower
+            if coordinator.has_blower
```

### 4. Test Fixture Correction

#### [MODIFY] [test_p25b85_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p25b85_adapter.py)

The `KDY_RAW` hex has byte 13 = `0xF5` (no blower). Real P25B85 captures consistently show `0xFD` (ozone=manual, heater=manual, blower=yes). Correction:

```diff
-    "1AFF013CD2B4FF08035E040604F54000"
+    "1AFF013CD2B4FF08035E040604FD4000"
```

Changing byte 13 from `0xF5` → `0xFD` (bit 3: 0→1) does not affect existing assertions because:
- `ozone_mode` stays `"manual"` (bit 7 unchanged).
- `heater_mode` stays `"manual"` (bit 4 unchanged).
- `blower` running state (byte 14, bit 3) is unaffected.

CRC bytes at the frame tail may need recalculation. Since no existing test validates `KDY_RAW`'s CRC directly (tests use `unescape_frame` → `parse_status`, bypassing CRC checks), this is safe. If CRC recalculation is needed, use `build_frame()` or compute offline.

Add a new assertion in `test_parse_status_core_fields`:

```python
assert result["blower_present"] is True
```

#### [MODIFY] [test_p25b37_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p25b37_adapter.py)

The P25B37 `KDY_RAW` has byte 13 = `0xF5`, which is correct for P25B37 (no blower). Add assertion:

```python
assert result["blower_present"] is False
```

#### [MODIFY] [test_entities_runtime.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_entities_runtime.py)

Add `has_blower` property to `DummyCoordinator` so switch tests continue to work:

```python
@property
def has_blower(self) -> bool:
    return self.adapter.has_blower
```

### 5. Not Changed

- **`ModelAdapter` protocol** (`base.py`): `has_blower: bool` stays as-is. It remains the static fallback.
- **P23/P20 adapters**: No `blower_present` field added to `parse_status()`. The coordinator fallback uses their static `has_blower`.
- **`P25B37Adapter.has_blower = False`**: Kept for backward compatibility. The dynamic flag takes precedence at runtime, but the class attribute serves as documentation and fallback.

## Verification Plan

### Automated Tests

```bash
.venv/bin/pytest -q -W ignore
.venv/bin/python tests/live/test_spa_controls.py --non-interactive
.venv/bin/ruff check custom_components/joyonway/
.venv/bin/ruff format --check custom_components/joyonway/
.venv/bin/mypy custom_components/joyonway/
```

### Manual Verification

- Confirm P25B85 test fixture parses `blower_present = True`.
- Confirm P25B37 test fixture parses `blower_present = False`.
- Confirm P23/P20 tests unchanged (no `blower_present` in data, fallback to adapter default).
