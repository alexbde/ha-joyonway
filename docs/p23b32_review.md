# PR Review: `feat/add-p23b32-support`

**Branch:** `feat/add-p23b32-support` → `main`
**Scope:** 27 files changed, +2,148 / −442 lines
**Commits:** 6 (squash-ready)

## Validation Summary

| Check | Result |
|---|---|
| `.venv/bin/pytest -q -W ignore` | ✅ 161 passed |
| `test_spa_controls.py --non-interactive` | ✅ 64 passed |
| `.venv/bin/ruff check` | ✅ All checks passed |
| `.venv/bin/mypy` | ✅ No issues in 17 source files |

## Overall Assessment

The PR is **well-structured and carefully implemented**. The adapter pattern extension is clean, the protocol-level changes (tail-only unescape, signature discrimination) are sound, and the P25B85 backward-compatibility has been preserved in all tested paths. The code passes all existing and new tests, lint, and type checks. Most of the issues below are edge-case hardening or polish items, not blockers.

## 🔴 Bugs / Risks

### 1. `_detect_model` config flow: default model returned on empty stream

**File:** [config_flow.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py) (lines ~55–75)

If the TCP connection succeeds but no frames are received before timeout (e.g. spa is powered off but bridge is up), `_detect_model` returns `DEFAULT_MODEL` (P25B85) instead of `None`. This silently creates a config entry for the wrong model.

```python
# Current:
detected_model = DEFAULT_MODEL  # falls through if no frames arrive
...
writer.close()
await writer.wait_closed()
return detected_model  # ← returns P25B85 even with zero data
```

> [!WARNING]
> **Fix:** Return `None` at the end of the function if no broadcast frame was found. The caller already treats `None` as `cannot_connect`:
> ```python
> return None  # no broadcast frame detected
> ```

### 2. `validate_frame` ordering change in coordinator — CRC validated before `is_broadcast`

**File:** [coordinator.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L541-L550)

The PR swaps the order: `is_broadcast()` is now checked **before** `validate_frame()`. This is correct and actually an improvement — broadcast-check is cheap (1 byte lookup) while `validate_frame` now involves full unescape + CRC. However, this creates a subtle behavioral change: on `main`, command-echo frames (non-broadcast) were still CRC-validated and logged/dropped silently. Now they're silently skipped without validation. This is fine in practice since command echoes are discarded anyway, but worth noting for protocol debugging purposes.

> [!NOTE]
> Not a bug — actually an optimization. Mentioning it for awareness.

### 3. `build_light_toggle_command(on=None)` — P25B85 ignores `on` param, P23B32 crashes

**File:** [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L467) / [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L328-L334)

The `ModelAdapter` protocol now defines `build_light_toggle_command(self, on: bool | None = None)`. The P25B85 adapter ignores the `on` parameter completely (it's always a toggle), while P23B32 uses discrete ON/OFF commands:

```python
# P23B32:
last_byte = 0x81 if on else 0x80  # on=None → 0x80 (OFF!)
```

If `on=None` is ever passed, P23B32 will silently send an OFF command. The `SpaLightSwitch` entity always passes `on=target` (a `bool`), so this won't trigger in practice. But the API contract is fragile.

> [!TIP]
> Consider adding a guard at the top of P23B32's `build_light_toggle_command`:
> ```python
> if on is None:
>     raise ValueError("P23B32 requires explicit on/off for light commands")
> ```

### 4. `P23B32Adapter.entity_descriptions()` imports `_P25B85_ENTITIES` at call time

**File:** [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L285-L309)

The P23B32 adapter builds its entity list by filtering the P25B85 entity list at runtime:
```python
base = [e for e in _P25B85_ENTITIES if e.key != "jets"]
```

This means P23B32 inherits **every** P25B85-specific entity, including `pump_low` and `pump_high` (which are P25B85-specific dual-speed booleans that P23B32 does not populate). These will show as permanently `None`/unavailable sensors.

> [!IMPORTANT]
> **Fix:** Filter out `pump_low` and `pump_high` alongside `jets`:
> ```python
> _P25B85_ONLY_KEYS = {"jets", "pump_low", "pump_high"}
> base = [e for e in _P25B85_ENTITIES if e.key not in _P25B85_ONLY_KEYS]
> ```
> Note: `pump_low`/`pump_high` are not in `_P25B85_ENTITIES` (they're parsed into the data dict but never have entity descriptions), so this is actually a non-issue for entities. However, the data dict will contain `pump_low: False` and `pump_high: False` keys from P25B85 entities if they ever get promoted to entity descriptions. Worth confirming there are no `pump_low`/`pump_high` entity descriptions in `_P25B85_ENTITIES` — **upon inspection, there aren't**, so this is safe currently.

### 5. `SpaSingleSpeedFan` — no `percentage` property (HA may show 0%)

**File:** [fan.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py#L241-L359)

`SpaSingleSpeedFan` doesn't expose `FanEntityFeature.SET_SPEED` and has no `percentage` property, which is correct for a single-speed fan. However, HA's fan card may still attempt to render a speed slider if the card UI doesn't distinguish properly. This is an HA UI issue, not a code issue — just worth monitoring.

> [!NOTE]
> Not a code bug, just an FYI.

## 🟡 Design Concerns

### 6. `JetDescription.type` as `str` — should be a literal or enum

**File:** [base.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/base.py#L9-L15)

```python
@dataclass(frozen=True)
class JetDescription:
    type: str  # "single" or "dual"
```

Using a plain `str` here is error-prone. A typo like `"duel"` would silently fall through in `async_setup_entry` (no fan entity created for that jet). Consider using a `Literal["single", "dual"]` or a `StrEnum`.

### 7. P25B85 `pump_byte_raw` renamed to `jet_byte_raw` — **breaking change for existing users**

**Files:** [p25b85.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L797), [sensor.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/sensor.py#L104), all translation files

The diagnostic sensor key was renamed from `pump_byte_raw` to `jet_byte_raw`. For existing P25B85 users:
- The sensor entity ID changes from `sensor.joyonway_spa_pump_byte_raw` to `sensor.joyonway_spa_jet_byte_raw`
- Any automations, dashboards, or history graphs referencing the old entity ID will break
- The old entity will appear as "unavailable" and a new one appears

> [!WARNING]
> This is a **breaking change** for existing P25B85 users. Options:
> 1. Accept it as a cosmetic rename in a feature release (minor semver bump) and document it in release notes
> 2. Keep the data dict key as `pump_byte_raw` for P25B85 and use `jet_byte_raw` only for P23B32 (preserves backwards compatibility)
> 3. Add a migration in `__init__.py` to update entity registry IDs

Since this is a diagnostic sensor (disabled by default), the blast radius is small. Option 1 with release notes is probably fine.

### 8. `build_ozone_mode_command` / `build_heater_mode_command` return `b""` for P23B32

**File:** [p23b32.py](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L487-L494)

Returning empty bytes `b""` for unsupported commands is a somewhat unusual pattern. The switch entity now catches this with `IntentBuildError`, which is good. However, the guard checks `if not cmd:` which is truthy for `b""` — this works, but the intent is clearer with an explicit `None` return or `NotImplementedError`.

The current approach works correctly but consider returning `None` and updating the protocol type to `bytes | None` for mode commands in the adapter protocol, or better yet, making those entities unavailable for models that don't support mode switching.

## 🟢 Positives

### Backwards Compatibility (P25B85) ✅
- All adapter method signatures were extended with **backward-compatible defaults** (e.g. `on: bool | None = None` for light, `jet_id` parameter prepended)
- All existing P25B85 tests pass without modification to assertions, only to call signatures
- The `unescape_frame` parameter rename from `full` to `unescape_full` is cleaner and all call sites updated
- The `validate_frame` reorder (check `is_broadcast` first) is a correct optimization

### Architecture ✅
- `JetDescription` data class is a clean way to describe heterogeneous pump/jet configs
- `SpaSingleSpeedFan` vs `SpaJetsFan` split is correct — single-speed fans shouldn't expose speed controls
- Auto-detection in config flow is a great UX improvement over manual model selection
- The `IntentBuildError` handling for unsupported commands is a good defensive pattern

### Test Coverage ✅
- 24 new P23B32 adapter tests covering all command builders, parsing, schedules, datetime
- All existing P25B85 tests updated for new signatures
- Dry-run simulator extended for P23B32 model simulation
- Entity runtime tests properly inject `JetDescription`

### Protocol Changes ✅
- Tail-only unescape policy (`unescape_full_frame=False`) is correctly threaded through `validate_frame` and `unescape_frame`
- CRC validation now correctly uses the model-appropriate unescape before CRC check
- Frame signature discrimination (`0x02` vs `0x03` at byte 8) is clean and well-documented

### Translation Coverage ✅
- All 5 translation files (en, de, fr, pl, strings.json) consistently updated with `jets_left`, `jets_right`, and `jet_byte_raw`

## 🔵 Style / Minor Items

| # | File | Issue |
|---|---|---|
| 1 | [p23b32.py:191-192](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L191-L192) | Single-speed jets use `"on"/"off"` strings, while dual-speed use `"off"/"low"/"high"`. The `SpaSingleSpeedFan.build_jets_command` maps `"low"/"high"` → `"on"`, which is correct, but the mismatch in state vocabularies across adapters could surprise future adapter authors. Consider documenting this explicitly in `ModelAdapter.get_jets_state()` |
| 2 | [p23b32.py:287-308](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L287-L308) | `entity_descriptions()` mutates a list copy with `.append()` — fine, but could use `[*base, desc1, desc2]` for immutability |
| 3 | [fan.py:1](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py#L1) | Module docstring still says "Joyonway P25B85 — jets speed control" — should be updated to mention multi-model support |
| 4 | [fan.py:110](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/fan.py#L110) | `SpaJetsFan._pending_timeout` log message says "Jets:" without including `self.jet.id`, while `SpaSingleSpeedFan` correctly includes it |
| 5 | [switch.py:1](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L1) | Module docstring still says "Joyonway P25B85" — should be generic |
| 6 | [test_p23b32_adapter.py:209-213](file:///Users/alex/repositories/alexbde/ha-joyonway/tests/test_p23b32_adapter.py#L209-L213) | Light ON and OFF test assertions are identical (`p_on.hex() == p_off.hex()`). The test asserts that the first 16 bytes match, but the actual ON vs OFF difference is in the 17th byte (0x81 vs 0x80) which is beyond the 16-byte window of `_frame_payload()`. The test doesn't actually verify the ON/OFF distinction |
| 7 | [p23b32.py:192](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p23b32.py#L192) | `heat_slot2_start` reports `(22, 15)` in the test, but byte `0x56` = 86 decimal. `86 & 0x3F = 22` and the next byte is `0x0F = 15`. The mock comment says "14 &#124; 0x40 = 0x56" but `14 | 0x40 = 0x4E`, not `0x56`. Actually, `0x56 = 86 = 22 | 0x40`. The comment at test line 56 says "14:15 (14 &#124; 0x40 = 0x56, 15)" which is mathematically wrong — should be "22:15 (22 &#124; 0x40 = 0x56, 15)". Test assertions are correct; the comment is wrong |

## Test Coverage Gaps

| Gap | Severity | Notes |
|---|---|---|
| `SpaSingleSpeedFan` entity runtime test | Low | No runtime test exercises the full `SpaSingleSpeedFan` lifecycle (optimistic state, timeout, coordinator update). The adapter tests cover command building, but the entity behavior is untested |
| `_detect_model` unit test | Medium | The new auto-detection logic in config_flow has no dedicated unit test. `test_config_flow.py` was updated but doesn't test P23B32 detection |
| P23B32 dry-run sim for jets_left/jets_right commands | Low | The dry-run simulator generates P23B32 broadcasts, but the 64 tests are all P25B85 (the `MODEL` default is `P25B85`). There are no simulation tests running P23B32 controls end-to-end |

## Summary

**Verdict: Approve with minor fixes.** The most impactful items to address before merge:

1. 🔴 **#1** — Fix `_detect_model` to return `None` when no frames are received (prevents silent wrong-model config)
2. 🟡 **#7** — Acknowledge the `pump_byte_raw` → `jet_byte_raw` rename as a breaking change in release notes
3. 🔵 **#6** — Fix the light ON/OFF test to actually verify byte 17 difference
4. 🔵 **#7** (style table) — Fix the wrong comment in test mock data (`0x56` = 22, not 14)
