# Fix All PR Review Findings on `feat/add-p23b32-support`

**Branch:** `feat/add-p23b32-support`
**Baseline:** All 161 unit tests, 64 dry-run sim tests, ruff, and mypy currently pass.
**Goal:** Fix all 8 review findings + clean-code audit, preserving all tests.

## Context

This PR adds support for the Joyonway P23B32 / P20B29 spa controller models to a Home Assistant integration. The integration uses a modular adapter pattern where each controller model implements a `ModelAdapter` protocol. The PR modifies 27 files. All fixes below must be made on the existing `feat/add-p23b32-support` branch.

> [!IMPORTANT]
> Read `AGENTS.md` at the repository root before making any changes. It contains critical coding guidelines, naming conventions, safety constraints, and testing requirements.

## Fix 1: `_detect_model` returns default model on empty stream

**File:** [config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py)

**Problem:** If TCP connects but no broadcast frames arrive before timeout (spa powered off, bridge up), `_detect_model()` returns `DEFAULT_MODEL` ("P25B85") instead of `None`. This silently creates a config entry with the wrong model.

**Fix:** Change the final return at the bottom of the function (after the `while` loop and `writer.close()`) from `return detected_model` to `return None`. The variable `detected_model` is only valid after a frame with byte[8] has been successfully read (which returns early from inside the loop). If we fall through the loop without finding a frame, no model was detected.

**Before:**
```python
writer.close()
await writer.wait_closed()
return detected_model  # ← BUG: returns P25B85 with zero data
```

**After:**
```python
writer.close()
await writer.wait_closed()
return None  # No broadcast frame detected within timeout
```

**Tests:** Add a test in [test_config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_config_flow.py) that mocks a TCP connection returning no data (empty reads), and asserts the config flow shows `cannot_connect` error rather than silently creating an entry.

## Fix 2: Rename `build_light_toggle_command` → split into two protocol-accurate methods

**Files:** [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py), [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py), [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py), [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py)

**Problem:** The method `build_light_toggle_command(on: bool | None = None)` is misleading. It says "toggle" but accepts an `on` parameter for discrete ON/OFF. P25B85 ignores the parameter (it's genuinely a toggle), while P23B32 uses it for discrete ON/OFF. If `on=None`, P23B32 silently sends OFF. This violates clean-code naming principles.

**Fix:** Replace the single method with two clearly named methods in the `ModelAdapter` protocol:

**In [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py):**
```python
def build_light_command(self, on: bool) -> bytes:
    """Build a light ON or OFF command.

    For toggle-only controllers (P25B85), this builds a toggle frame
    regardless of the `on` value — the entity layer handles no-op detection.
    For discrete-command controllers (P23B32), this builds the appropriate
    ON or OFF frame.
    """
    ...
```

Remove the old `build_light_toggle_command` from the protocol entirely. The new method name `build_light_command` is consistent with `build_heater_command`, `build_blower_command`, etc.

**In [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py):**
```python
def build_light_command(self, on: bool) -> bytes:
    """Build a light command. P25B85 uses toggle; `on` is ignored."""
    return self._build_button_command(btn_group=0x40, btn_action=0x40)
```

**In [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py):**
```python
def build_light_command(self, on: bool) -> bytes:
    """Build a discrete light ON or OFF command for P23B32."""
    from ..protocol import build_frame

    last_byte = 0x81 if on else 0x80
    payload = bytearray([
        0x01, 0x30, 0x10, 0x3C, 0xA1, 0x00, 0xA1,
        0x00, 0x00, 0x00, 0x40, 0x40, 0x02, 0x04, 0x00, 0x00, last_byte,
    ])
    return build_frame(bytes(payload))
```

**In [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py) (SpaLightSwitch._send_toggle → _send_light_intent):**
- Rename `_send_toggle` to `_send_light_intent`
- Update the `_build_light` closure to call `coordinator.adapter.build_light_command(on=target)` instead of `build_light_toggle_command(on=target)`
- Update log messages from "toggle" to "light intent"
- Update docstrings for `async_turn_on` / `async_turn_off` from "toggle if currently off/on" to "send light ON/OFF command"
- The `SpaLightSwitch` class docstring should say "light ON/OFF command" instead of "toggle command"
- The comments inside `_build_light` should be updated. The no-op detection logic stays the same (if data matches target, return None), but the comment should explain this is "intent coalescing" rather than "toggle cancellation"

**In tests:** Update all test references from `build_light_toggle_command` to `build_light_command`. In the DummyAdapter stubs in [test_entities_runtime.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_entities_runtime.py) and any other test files that stub the adapter.

## Fix 3: Decouple P23B32 `entity_descriptions()` from P25B85

**File:** [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py) lines 285–309

**Problem:** `P23B32Adapter.entity_descriptions()` imports and filters `_P25B85_ENTITIES`, creating a hidden coupling between adapters. There is no inheritance relationship between models, so this code creates a fragile dependency — any change to P25B85's entity list silently affects P23B32.

**Fix:** Define an independent `_P23B32_ENTITIES` list at module level in `p23b32.py`. Copy the relevant entity descriptions from P25B85 and adjust them:

1. Create a `_P23B32_ENTITIES: list[SpaEntityDescription]` list at the bottom of `p23b32.py` (before the class or after it, following the same pattern as P25B85)
2. Include all the same sensors as P25B85 **except** the `jets` sensor (key="jets")
3. **Add** two sensors: `jets_left` (options: `["off", "on"]`) and `jets_right` (options: `["off", "on"]`)
4. Use `jets_byte_raw` as the diagnostic sensor key (unified across all models — see Fix 6)

The `entity_descriptions()` method becomes:
```python
def entity_descriptions(self) -> list[SpaEntityDescription]:
    return _P23B32_ENTITIES
```

No imports from `p25b85` needed at all. Remove the `from .p25b85 import _P25B85_ENTITIES` line.

## Fix 4: Add `percentage` property to `SpaSingleSpeedFan`

**File:** [fan.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py) lines 241–359

**Problem:** `SpaSingleSpeedFan` has no `percentage` property and doesn't include `FanEntityFeature.SET_SPEED`. This caused issues before on the P25B85 fan and could lead to HA UI rendering problems.

**Fix:** Make `SpaSingleSpeedFan` as similar to `SpaJetsFan` as reasonable for a single-speed entity:

1. Add `_attr_speed_count = 1` to the class
2. Add `FanEntityFeature.SET_SPEED` to `_attr_supported_features`:
   ```python
   _attr_supported_features = (
       FanEntityFeature.SET_SPEED
       | FanEntityFeature.TURN_ON
       | FanEntityFeature.TURN_OFF
   )
   ```
3. Add a `percentage` property:
   ```python
   @property
   def percentage(self) -> int | None:
       state = self._get_jets_state()
       return 100 if state != "off" else 0
   ```
4. Add a helper `_get_jets_state()` method (same pattern as `SpaJetsFan`):
   ```python
   def _get_jets_state(self) -> str:
       if self._pending_state is not None:
           return self._pending_state
       return self.coordinator.adapter.get_jets_state(
           self.coordinator.data or {}, self.jet.id
       )
   ```
5. Add `async_set_percentage` method:
   ```python
   async def async_set_percentage(self, percentage: int) -> None:
       if percentage == 0:
           await self.async_turn_off()
       else:
           await self.async_turn_on()
   ```

**Tests:** Add a test case for `SpaSingleSpeedFan` in [test_entities_runtime.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_entities_runtime.py) or [test_fan_entity_runtime.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_fan_entity_runtime.py) that verifies:
- `speed_count == 1`
- `percentage` returns 0 when off, 100 when on
- `SET_SPEED`, `TURN_ON`, `TURN_OFF` features are supported
- `async_set_percentage(0)` turns off, `async_set_percentage(50)` and `async_set_percentage(100)` turn on

## Fix 5: Refactor `JetDescription.type` from `str` to `StrEnum`

**File:** [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py)

**Problem:** `JetDescription.type` is a plain `str` ("single" or "dual"), which is error-prone. A typo would silently fail.

**Fix:** Define a `JetType` enum:

```python
from enum import StrEnum

class JetType(StrEnum):
    """Jet speed capability."""
    SINGLE = "single"
    DUAL = "dual"
```

Update `JetDescription`:
```python
@dataclass(frozen=True)
class JetDescription:
    """Describes a jet/pump exposed by a model adapter."""

    id: str
    name: str
    type: JetType
```

Update all usages:
- [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py): `JetDescription(id="jets", name="Jets", type=JetType.DUAL)`
- [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py): `JetDescription(id="jets_left", name="Jets Left", type=JetType.SINGLE)` and similar for `jets_right`
- [fan.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py) `async_setup_entry`: Change `jet.type == "dual"` to `jet.type == JetType.DUAL` and `jet.type == "single"` to `jet.type == JetType.SINGLE`
- All test files that create `JetDescription` instances

Also update the docstring of `JetDescription` from "Describes a pump" to "Describes a jet/pump".

## Fix 6: Unify diagnostic sensor key to `jets_byte_raw`

**Files:** [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py), [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py), [sensor.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/sensor.py), all translation files

**Problem:** The PR renamed `pump_byte_raw` to `jet_byte_raw`, but the correct user-facing term is "jets" (plural), and consistency across both models is more important than backward compatibility for a disabled-by-default diagnostic sensor.

**Fix — Unified `jets_byte_raw` key:**
1. **In P25B85's `parse_status()`** (line ~307 of p25b85.py): Change the data dict key from `"jet_byte_raw"` to `"jets_byte_raw"`.
2. **In P23B32's `parse_status()`**: Change the data dict key from `"jet_byte_raw"` to `"jets_byte_raw"`.
3. **In P25B85 entity descriptions** (`_P25B85_ENTITIES`, line ~797): Change `key="jet_byte_raw"` to `key="jets_byte_raw"`, `name="Jets byte (raw)"`.
4. **In P23B32 entity descriptions** (the new independent `_P23B32_ENTITIES` list from Fix 3): Use `key="jets_byte_raw"`, `name="Jets byte (raw)"`.
5. **In [sensor.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/sensor.py):** Ensure the hex formatting set references `"jets_byte_raw"` (not `"jet_byte_raw"` or `"pump_byte_raw"`).
6. **In all translation files** (strings.json, en.json, de.json, fr.json, pl.json): Rename the key from `"jet_byte_raw"` to `"jets_byte_raw"` and update the name to "Jets byte (raw)" / translated equivalents.

**In tests:** Update assertions in [test_p25b85_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p25b85_adapter.py) and [test_p23b32_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p23b32_adapter.py) from `result["jet_byte_raw"]` to `result["jets_byte_raw"]`.

## Fix 7: Fix light ON/OFF test and mock data comment

**File:** [test_p23b32_adapter.py](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p23b32_adapter.py)

**Problem A (test):** The `test_build_light_toggle_command` test (will be renamed to `test_build_light_command`) only checks the first 16 bytes via `_frame_payload()`, but the ON/OFF difference is at byte index 16 (17th byte: `0x81` vs `0x80`). Both ON and OFF assertions are identical — the test doesn't actually verify the distinction.

**Fix:** Update `_frame_payload()` to accept a configurable length, or add a dedicated assertion for the full payload. The cleanest approach:
```python
def _frame_payload(frame: bytes, length: int = 16) -> bytes:
    """Extract the unescaped payload from a wire frame."""
    return pseudo_unescape(frame[1:-1])[:length]
```

Then in the test:
```python
def test_build_light_command(adapter: P23B32Adapter) -> None:
    on_frame = adapter.build_light_command(on=True)
    p_on = _frame_payload(on_frame, length=17)
    assert p_on[16] == 0x81  # ON marker

    off_frame = adapter.build_light_command(on=False)
    p_off = _frame_payload(off_frame, length=17)
    assert p_off[16] == 0x80  # OFF marker

    # First 16 bytes are identical
    assert p_on[:16] == p_off[:16]
```

**Problem B (comment):** Line 56 of the mock data comment says `"14:15 (14 | 0x40 = 0x56, 15)"` but `14 | 0x40 = 0x4E`, not `0x56`. The actual value `0x56 = 86 = 22 | 0x40`, so the slot2 start time is `22:15`, not `14:15`.

**Fix:** Correct the comment:
```python
# s2 start: 22:15 (22 | 0x40 = 0x56, 15), end: 16:45 (16, 45) -> 56 0F 10 2D
```

And update the test assertion comment at line 192 to say `heat_slot2_start == (22, 15)` (which it already does — only the mock comment is wrong).

## Fix 8: Systematic `pump` → `jets` naming cleanup

**File:** Primarily [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py), [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py)

**Problem:** The codebase has inconsistent naming: end-user facing text should use "jets" but internal code still uses "pump" in many places. "Jets" is the end-user term; Fix 6 already unifies the diagnostic sensor key to `jets_byte_raw`.

**What to rename (internal code):**

In [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py):
- `IDX_PUMP_BYTE` → `IDX_JET_BYTE` (line 44)
- `MASK_PUMP_LOW` → `MASK_JET_LOW` (line 104)
- `MASK_PUMP_HIGH` → `MASK_JET_HIGH` (line 105)
- `_PUMP_TARGET_BYTES` → `_JET_TARGET_BYTES` (line 204)
- `pump_b8` parameter in `_build_button_command` → `jet_b8` (line 426, 455, 481)
- Comments: "pump" → "jet" where referring to the component (e.g. "Pump masks" → "Jet masks", "Pump transition encodings" → "Jet transition encodings")
- The `pump_low` and `pump_high` keys in the data dict should be renamed to `jet_low` and `jet_high` (these are internal data dict keys, not entity description keys)

In [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py):
- `IDX_PUMP_BYTE` → `IDX_JET_BYTE` (line 26)

In [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py):
- `JetDescription` docstring: "Describes a pump" → "Describes a jet/pump" (already noted in Fix 5)

**After this fix + Fix 6, no `pump` references should remain** in the codebase except in protocol-level comments where "pump" refers to the physical hardware component (acceptable as a technical term in comments documenting the RS485 protocol).

**Update all affected tests** to use the new constant names.

## Fix 9: Additional clean-code findings

### 9a. Module docstrings

Update the following module docstrings to be model-agnostic:
- [fan.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py) line 1: "Fan platform for Joyonway P25B85" → "Fan platform for Joyonway spa controllers"
- [switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py) line 1: "Switch platform for Joyonway P25B85" → "Switch platform for Joyonway spa controllers"

### 9b. `SpaJetsFan._pending_timeout` missing jet ID in log

[fan.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py) line 109–111: The log message says `"Jets: command not confirmed..."` without including `self.jet.id`, while `SpaSingleSpeedFan` at line 293 correctly includes it. Fix:
```python
_LOGGER.warning(
    "Jets %s: command not confirmed by spa within %ds, reverting state",
    self.jet.id,
    int(OPTIMISTIC_TIMEOUT_SECONDS),
)
```

### 9c. `_send_toggle` naming in switch.py

[switch.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py) lines 182–201: Rename `_send_toggle` → `_send_light_intent` for consistency with other entity intent methods (`_submit_heater_intent`, `_submit_blower_intent`, etc.). Update class docstring and comments referencing "toggle" semantics. The `_cmd_lock` and its guard comments ("toggle already in-flight") should be updated to "command already in-flight".

### 9d. `build_ozone_mode_command` / `build_heater_mode_command` return type

[p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py) lines 487–494: These return `b""` for unsupported operations. This is caught downstream by `if not cmd: raise IntentBuildError(...)` in switch.py. The empty bytes are truthy-falsy equivalent to `None` for this check, but semantically `None` is clearer for "not supported". However, changing the return type in the `ModelAdapter` protocol from `bytes` to `bytes | None` would be a broader change. Since the current approach works and is caught, leave as-is but ensure the P23B32 entities for manual ozone/heater mode switches remain available in the entity list — they will fail gracefully with a clear error message if toggled, which is acceptable since the protocol doesn't document these commands for P23.

## Fix 10: Documentation updates

The following documentation files need updates to stay consistent with the code changes.

### 10a. [protocol.md](file:///Users/alex/repositories/alexbde/ha-joyonway/docs/protocol.md)

**Section 5.1, row "Light Control" (line 130):** The P25B85 column currently documents both a "Toggle Command" and "Discrete ON/OFF" (marked `[❌]`). After Fix 2, the adapter method is renamed from `build_light_toggle_command` to `build_light_command`. The protocol doc should stay as-is since it documents the *wire protocol*, not the Python API. However, update **Section 7, Behavioral Notes (line 158)** to clarify the software API change:

**Current (line 158):**
```
*   **Light is a toggle (P25 only):** The P25 panel sends the same frame for ON and OFF.
    Software must track state and avoid sending when state is unknown. P23 has distinct ON/OFF frames.
```

**Updated:**
```
*   **Light command behavior differs by model:** The P25B85 panel sends the same toggle frame
    regardless of ON or OFF intent — the integration's entity layer handles no-op detection by
    tracking the current state. The P23B32 / P20B29 controllers use distinct ON and OFF command
    frames (byte 16: `0x81` = ON, `0x80` = OFF). The adapter's `build_light_command(on: bool)`
    method abstracts this difference.
```

**Section 3.3 (line 76):** Currently says `inner = payload[16 bytes]`. Update to note that some commands use 17-byte payloads:
```
inner = payload[16 or 17 bytes] + crc[4 bytes LE]
```

### 10b. [README.md](file:///Users/alex/repositories/alexbde/ha-joyonway/README.md)

**Line 52, Features section:** Currently says `**Light** on/off via toggle command`. Update to be model-agnostic:
```
- **Light** on/off control
```

**Line 106, Entities table, Jets row:** Currently says `Pump speed control (0% / 50% / 100%)`. This only describes P25B85's dual-speed fan. Update to note both models:
```
| **Jets** | Fan | Pump speed control: P25B85 uses 3-speed (0% / 50% / 100%), P23B32 / P20B29 uses ON/OFF (0% / 100%) per pump |
```
Or more concisely, since the row is already generic:
```
| **Jets** | Fan | Jets / pump speed control |
```

**Line 144, Diagnostics table:** Currently says `**Pump byte (raw)**`. Update to `**Jets byte (raw)**` to match the unified `jets_byte_raw` key.

### 10c. [CONTRIBUTING.md](file:///Users/alex/repositories/alexbde/ha-joyonway/CONTRIBUTING.md)

**Line 45–48, "Adding a model adapter" section:** Currently lists simplified method names:
```
- `parse_status(frame: bytes) -> dict | None`
- `entity_descriptions() -> list[SpaEntityDescription]`
- `build_command(...) -> bytes`
```

The `build_command(...)` line is misleading — there is no single `build_command` method. Update to be more specific and accurate:
```
- `parse_status(frame: bytes) -> dict | None` — extract state dict from broadcast frame
- `entity_descriptions() -> list[SpaEntityDescription]` — define exposed entities
- `build_light_command(on: bool) -> bytes` and other `build_*_command()` methods — construct command frames
- `jets: list[JetDescription]` — declare jet/pump configurations with `JetType` enum
```

**Line 61, Diagnostic sensor list:** Currently says `**Jet byte (raw)**`. Update to use the unified name:
```
- **Jets byte (raw)**
```

**Line 106, Architecture section:** Currently says `raw byte states (heater_byte_raw, jet_byte_raw, etc.)`. Update to the unified key:
```
raw byte states (`heater_byte_raw`, `jets_byte_raw`, etc.)
```

### 10d. [AGENTS.md](file:///Users/alex/repositories/alexbde/ha-joyonway/AGENTS.md)

**Line 51, Diagnostics Redaction constraint:** Currently says `export raw byte states (heater_byte_raw, jet_byte_raw, etc.)`. Update to the unified key:
```
export raw byte states (`heater_byte_raw`, `jets_byte_raw`, etc.)
```

No other AGENTS.md changes needed — the architectural constraints (intent queue, sync-frame pacing, etc.) are model-agnostic and still correct.

## Verification Plan

### Automated Tests
After all fixes, run:
```bash
.venv/bin/ruff check custom_components/joyonway/
.venv/bin/ruff format --check custom_components/joyonway/
.venv/bin/mypy custom_components/joyonway/
.venv/bin/pytest -q -W ignore
.venv/bin/python tests/live/test_spa_controls.py --non-interactive
```

All must pass: 161+ unit tests (may increase with new tests), 64 dry-run sim tests, no lint or type errors.

### Manual Checklist
- [ ] Verify no remaining `build_light_toggle_command` references anywhere in the codebase
- [ ] Verify `jets_byte_raw` is the sole diagnostic sensor key in both P25B85 and P23B32 (no `pump_byte_raw` or `jet_byte_raw` remains)
- [ ] Verify no `from .p25b85 import` in `p23b32.py`
- [ ] Grep for remaining `pump` references — only protocol-level comments should remain
- [ ] Verify protocol.md Section 7 accurately describes the new `build_light_command` API
- [ ] Verify README.md Features and Entities sections are model-agnostic
- [ ] Verify CONTRIBUTING.md adapter guide references the correct method names
- [ ] Verify AGENTS.md diagnostics constraint uses `jets_byte_raw`
