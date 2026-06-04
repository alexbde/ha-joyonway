# Code Review: ha-joyonway Integration

## Baseline Status

| Check | Result |
|---|---|
| `ruff check` | ✅ All checks passed |
| `mypy` | ✅ No issues in 16 source files |
| `pytest` | ✅ 140 passed in 0.63s |

---

## Verdict Summary

This is a **well-engineered integration** that is clearly the product of thoughtful reverse-engineering work. The architecture is sound — protocol parsing is properly decoupled from entity logic, the coordinator pattern prevents bus contention, and the intent queue with coalescing is an above-average approach to RS485 command management. The code passes all three static-analysis checks cleanly.

That said, the review uncovered several findings ranging from substantive architectural concerns to minor polish items. None are blockers to daily use, but several should be addressed before a wider release.

**Overall rating: 8/10** — solid for a custom HACS integration; a few targeted fixes would bring it to production-grade.

---

## Critical Findings

### C1. `validate_frame()` does not verify CRC — incoming frames are not integrity-checked

**Files:** [protocol.py:117-129](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/protocol.py#L117-L129), [coordinator.py:526](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L526)

`validate_frame()` only checks delimiters and minimum length. It does **not** validate the CRC-32. This means corrupted frames (noise on RS485, partial reads, or collision artifacts) will be passed to the adapter for parsing. While `parse_status()` has some bounds-checking, it has no checksum gate.

The CRC computation is already implemented in `compute_crc()` but is only used **outbound** (in `build_frame`). The inbound path never calls it.

> [!WARNING]
> On a noisy RS485 bus (especially at 9600 baud with cheap TCP bridges), corrupted frames are routine. Without CRC validation, the integration can misparse a corrupted broadcast and update HA entities with garbage values (e.g., wrong temperature). This is a data-integrity risk.

**Recommendation:**
Add CRC validation to the inbound path. A practical approach since the CRC XOR-out was cracked from same-session captures:

```python
# In validate_frame(), after delimiter checks:
def validate_frame(frame: bytes) -> bool:
    if len(frame) < 4:
        return False
    if frame[0] != FRAME_START or frame[-1] != FRAME_END:
        return False
    # CRC validation for frames with enough payload
    unescaped = pseudo_unescape(frame[1:-1])
    if len(unescaped) >= 20:  # 16-byte payload + 4-byte CRC
        payload = unescaped[:16]
        crc_received = struct.unpack("<I", unescaped[16:20])[0]
        crc_expected = compute_crc(payload)
        if crc_received != crc_expected:
            return False
    return True
```

> [!IMPORTANT]
> Note: This recommendation applies to **command response / known 20-byte frames**. Broadcast frames (60+ bytes) may use a different CRC scope. If the CRC only covers the first 16 bytes of a broadcast, you need to validate that segment specifically. If the broadcast CRC covers the full payload, the existing `compute_crc()` (which enforces `len(payload) == 16`) must be generalized. Investigate which bytes the broadcast CRC covers before implementing.

---

## Medium Findings

### M1. `_check_clock_drift` is called synchronously in the reader loop — `time.monotonic()` is fine but `dt_util.now()` and intent submission happen on every frame

**File:** [coordinator.py:486](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L486)

`_check_clock_drift()` is called on **every successfully parsed broadcast frame** (roughly every 1-2 seconds). While the cooldown check exits early most of the time, the function still calls `dt_util.now()` and constructs `datetime` comparisons on every invocation path past the cooldown check. This is not a hot-path problem per se, but it's wasteful.

More importantly, when the drift threshold *is* exceeded, the method synchronously submits two intents to the intent queue from inside the reader loop's `_try_parse_buffer` → `_check_clock_drift` call chain. This is safe because `submit()` is non-async, but it tightly couples the reader loop with command-side logic.

**Recommendation:**
Move the clock check to a periodic callback (e.g., every 60s) rather than running it on every broadcast:

```python
# In async_setup():
self._clock_check_unsub = async_track_time_interval(
    hass, self._periodic_clock_check, timedelta(seconds=60)
)
```

---

### M2. Duplicate `_get_coordinator_heater_state()` and `_build_heater()` logic

**Files:** [switch.py:264-272](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L264-L272), [climate.py:95-104](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/climate.py#L95-L104), [switch.py:298-309](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L298-L309), [climate.py:233-244](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/climate.py#L233-L244)

The heater state derivation logic (`heater_enabled` fallback to status check) is duplicated in three places:
1. `SpaHeaterSwitch._get_coordinator_heater_state()`
2. `SpaClimate._get_coordinator_heater_state()`
3. Inline in `_build_heater()` closures in both files

Similarly, the `_build_heater()` closure is near-identical between `SpaHeaterSwitch` and `SpaClimate`.

**Recommendation:**
Extract to a shared helper. The adapter is the natural home for state derivation:

```python
# In P25B85Adapter or a shared helper module:
def is_heater_enabled(data: dict | None) -> bool | None:
    if data is None:
        return None
    val = data.get("heater_enabled")
    if val is None:
        status = data.get("status")
        if status is not None:
            val = status in ("standby", "circulation", "heating")
    return val
```

---

### M3. `_SpaTargetStateSwitch` and `SpaLightSwitch` share almost identical optimistic-state machinery

**Files:** [switch.py:53-173](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L53-L173), [switch.py:176-241](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L176-L241)

`SpaLightSwitch` duplicates the optimistic state pattern (pending state, arm/cancel timeout, revert on timeout) instead of inheriting from `_SpaTargetStateSwitch`. The only difference is the toggle-lock guard and the `_broadcast_confirms_pending()` logic. This violates DRY and makes the two patterns drift over time.

**Recommendation:**
Refactor `SpaLightSwitch` to extend `_SpaTargetStateSwitch` with a toggle-lock mixin or a `_is_toggle_command = True` flag. Override only `_broadcast_confirms_pending()` and the send method.

---

### M4. `IntentQueue._process_group` directly accesses `coordinator._on_data_callbacks` — encapsulation breach

**File:** [coordinator.py:189](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L189), [coordinator.py:252](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L252)

The intent queue reaches into the coordinator's private `_on_data_callbacks` list to register/unregister convergence listeners. This creates a fragile bidirectional coupling.

**Recommendation:**
Expose `register_data_callback()` / `unregister_data_callback()` methods on the coordinator as a proper API surface.

---

### M5. `async_set_temperature` missing type annotation for `**kwargs`

**File:** [climate.py:263](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/climate.py#L263)

```python
async def async_set_temperature(self, **kwargs) -> None:
```

This is the only untyped signature in the entire codebase. Should be `**kwargs: Any` for mypy strict mode consistency. Currently passes because mypy isn't running with `--strict`.

---

### M6. `PLATFORMS` uses `list[str]` instead of `list[Platform]`

**File:** [const.py:46](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/const.py#L46)

HA Core convention uses `Platform` enum:

```python
from homeassistant.const import Platform

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.FAN,
    Platform.CLIMATE,
    Platform.TIME,
]
```

This is a soft convention (strings work), but using `Platform` enum adds type safety and is what HA Core reviewers expect.

---

### M7. `CONF_HOST` / `CONF_PORT` imported from both `homeassistant.const` and local `const.py`

**Files:** [config_flow.py:15](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/config_flow.py#L15), [const.py:8-9](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/const.py#L8-L9), [diagnostics.py:11-12](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/diagnostics.py#L11-L12)

`config_flow.py` imports `CONF_HOST` and `CONF_PORT` from `homeassistant.const`, while `__init__.py` imports them from local `.const`. Meanwhile, `diagnostics.py` re-declares them as string literals. These are the same values, but the inconsistency creates confusion.

**Recommendation:**
Use `homeassistant.const.CONF_HOST` / `CONF_PORT` everywhere (they're standard HA constants). Remove the re-declarations in `const.py` and `diagnostics.py`.

---

### M8. `sensor.py` `native_value` return type annotation is missing

**File:** [sensor.py:93](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/sensor.py#L93)

```python
@property
def native_value(self):
```

Should be:

```python
@property
def native_value(self) -> StateType | datetime | None:
```

(or whatever union covers the actual return types). This is the only property in the codebase without a return type.

---

### M9. `entry.runtime_data` is untyped — use `type[ConfigEntry]` parameterization

**Files:** [__init__.py:30](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/__init__.py#L30), all `async_setup_entry` platform functions

HA 2024.x+ supports typed runtime data via:

```python
type JoyonwayConfigEntry = ConfigEntry[JoyonwayP25B85Coordinator]
```

Then use `JoyonwayConfigEntry` as the type throughout. This eliminates the `entry.runtime_data` cast pattern like:

```python
coordinator: JoyonwayP25B85Coordinator = entry.runtime_data  # type: ignore
```

---

## Minor Findings

### m1. `find_frames_with_indices` nested while-else — hard to follow

**File:** [protocol.py:50-66](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/protocol.py#L50-L66)

The `while`/`else` construct is rarely used in Python and can confuse contributors. Consider refactoring to a standard sentinel-based pattern.

---

### m2. Hardcoded sync frame bytes — should be a named constant

**File:** [coordinator.py:522](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L522)

```python
if raw_frame == b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d":
```

This magic constant appears in the coordinator and is referenced in AGENTS.md. It should be a named constant in `protocol.py` or `const.py`:

```python
SYNC_FRAME = b"\x1a\x01\x20\x08\x3c\xaa\x10\x00\x00\x6b\x73\xe4\xb9\x1d"
```

---

### m3. `asyncio.ensure_future()` should be `asyncio.create_task()`

**File:** [coordinator.py:133](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L133), [coordinator.py:153](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/coordinator.py#L153)

`asyncio.ensure_future()` is a legacy API. Modern Python 3.10+ (and HA Core) convention is `asyncio.create_task()`. In HA context specifically, `self.hass.async_create_task()` would be even better for proper task tracking/cancellation.

However, note that `IntentQueue` doesn't have a direct `hass` reference — it accesses it through `self._coordinator.hass`. If you switch to `create_task()`, this works fine. If you want HA-managed tasks, pipe `self._coordinator.hass.async_create_task()`.

---

### m4. No `async_will_remove_from_hass` on `SpaAutoClockSyncSwitch`

**File:** [switch.py:583-617](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/switch.py#L583-L617)

Not technically needed since it doesn't create tasks, but all other switch entities implement it. For consistency and future-proofing, consider adding it.

---

### m5. `_TRAILER_LEN` defined as a local variable inside `parse_status()`

**File:** [p25b85.py:380](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L380)

`_TRAILER_LEN = 5` uses a leading-underscore constant naming convention but is a local variable. Move to module-level with the other constants for clarity.

---

### m6. `hashlib.md5` usage for `unmapped_bytes_hash`

**File:** [p25b85.py:389](file:///Users/alex/repositories/alexbde/ha-joyonway/custom_components/joyonway/adapters/p25b85.py#L389)

MD5 is fine for a diagnostic fingerprint, but on Python 3.9+ with FIPS-enabled systems it may raise. Use `hashlib.md5(…, usedforsecurity=False)` to be safe:

```python
hashlib.md5(bytes(digest_input), usedforsecurity=False).hexdigest()[:8]
```

---

## What's Done Well ✅

These are aspects that are **notably strong** and worth calling out:

| Area | Assessment |
|---|---|
| **Protocol decoupling** | Protocol parsing (`protocol.py`) is fully decoupled from HA entity logic. The adapter pattern allows multi-model support without touching entities. |
| **Coordinator pattern** | Single persistent TCP connection with background reader loop. Entities never poll the bus. Data flows unidirectionally: bus → coordinator → entities. |
| **Intent queue with coalescing** | The `IntentQueue` is an unusually mature approach for a custom integration. Coalescing rapid edits, no-op detection, and sequential drain under lock are all correct. |
| **Sync-frame pacing** | Waiting for the idle sync frame before writing is the correct approach for shared RS485 bus timing. The 30ms post-sync delay is well-calibrated. |
| **Exponential backoff reconnect** | Proper backoff from 1s → 30s cap, reset on successful connect. |
| **Grace-mode availability** | 10s grace window prevents UI flicker on brief WiFi dropouts while bridge connectivity sensor bypasses it for honest status. |
| **Optimistic UI with snap-back** | All writable entities use optimistic state with confirmation-on-broadcast and timeout-based snap-back. This is the correct pattern for high-latency device control. |
| **Config flow with connection test** | Proper UI config flow with live TCP test before creating the entry. |
| **Teardown lifecycle** | `async_unload_entry` → `async_shutdown()` properly closes TCP, cancels reader task, drains intent queue, and cancels reconnect tasks. |
| **Logging discipline** | DEBUG for raw frames and internal transitions, WARNING for timeouts, ERROR for failures. No excessive logging at INFO level. |
| **No unsolicited writes** | The integration never writes to the bus on startup — strictly read-only until user action. Respects the safety constraint. |
| **Buffer overflow protection** | `coordinator.py:493` truncates the TCP buffer at 8192 bytes to prevent unbounded memory growth on stuck connections. |

---

## Summary of Actionable Items

| Priority | ID | Item | Effort |
|---|---|---|---|
| 🔴 Critical | C1 | Validate CRC on inbound broadcast frames | Medium |
| 🟡 Medium | M1 | Move clock drift check off the hot reader loop | Low |
| 🟡 Medium | M2 | Extract shared heater state derivation | Low |
| 🟡 Medium | M3 | Unify `SpaLightSwitch` with `_SpaTargetStateSwitch` | Medium |
| 🟡 Medium | M4 | Encapsulate `_on_data_callbacks` access | Low |
| 🟡 Medium | M5 | Add `**kwargs: Any` type annotation | Trivial |
| 🟡 Medium | M6 | Use `Platform` enum for `PLATFORMS` | Trivial |
| 🟡 Medium | M7 | Consolidate `CONF_HOST`/`CONF_PORT` imports | Trivial |
| 🟡 Medium | M8 | Type `native_value` return | Trivial |
| 🟡 Medium | M9 | Use typed `ConfigEntry` with runtime data | Low |
| 🔵 Minor | m1 | Refactor `while`/`else` in frame finder | Trivial |
| 🔵 Minor | m2 | Extract sync frame magic constant | Trivial |
| 🔵 Minor | m3 | Replace `ensure_future` with `create_task` | Trivial |
| 🔵 Minor | m4 | Add `async_will_remove_from_hass` to `SpaAutoClockSyncSwitch` | Trivial |
| 🔵 Minor | m5 | Move `_TRAILER_LEN` to module scope | Trivial |
| 🔵 Minor | m6 | Add `usedforsecurity=False` to MD5 call | Trivial |
