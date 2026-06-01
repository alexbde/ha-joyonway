# Priority 3: Diagnostics Enrichment — Implementation Plan

> **Goal:** Capture and expose controller diagnostic metadata from broadcast
> frames as HA diagnostic entities, starting with firmware/version fields
> and expanding to raw frame telemetry useful for debugging and future
> protocol work.

## 1. Scope

### Phase A — Broadcast frame metadata (low risk, no writes)
Extract read-only diagnostic values from the existing broadcast stream.
All new entities are `entity_category="diagnostic"` and
`enabled_by_default=False` to keep the default UI clean.

### Phase B — Unmapped byte exploration tool (dev-only)
A capture tool to identify and track changes in currently unmapped broadcast
bytes, enabling discovery of firmware version fields or other metadata.

## 2. New Diagnostic Entities

| Entity | Platform | Key | Type | Description |
|--------|----------|-----|------|-------------|
| Heater byte (raw) | sensor | `heater_byte_raw` | str (hex) | Raw byte 14 value shown as hex (`0x55`); useful for debugging unknown heater states |
| Pump byte (raw) | sensor | `pump_byte_raw` | str (hex) | Raw byte 12 value shown as hex; helps identify undocumented pump states |
| Ozone mode byte (raw) | sensor | `ozone_mode_byte_raw` | str (hex) | Raw byte 13 value shown as hex; may contain undiscovered flags beyond bit 7 |
| Activity byte (raw) | sensor | `activity_byte_raw` | str (hex) | Raw byte 28 value shown as hex; known bits: 3=blower, 5=activity |
| Light/cycle byte (raw) | sensor | `light_cycle_byte_raw` | str (hex) | Raw byte 17 value shown as hex; known bits: 0=light, 7=heating-cycle |
| Frame length | sensor | `frame_length` | int | Logical unescaped frame length (bytes) |
| Unknown bytes hash | sensor | `unmapped_bytes_hash` | str | Short hash of currently unmapped payload byte positions; changes signal new protocol behavior |

All raw byte sensors display as hex strings (e.g., `0x55`) for readability
in logs and dashboards, using `native_value` formatting in `sensor.py`.
(`parse_status()` still stores source bytes as ints.)

## 3. Implementation Steps

### Step 1: Extend `parse_status()` in `p25b85.py`

Add raw byte values and frame metadata to the returned dict. These are
already available in the frame — no new byte positions need to be read.
`frame` here refers to the logical, unescaped frame (including delimiters),
which is what the adapter already parses against.

```python
# Add to the result dict in parse_status():
result["heater_byte_raw"] = heater_byte
result["pump_byte_raw"] = pump_byte
result["ozone_mode_byte_raw"] = ozone_mode_byte
result["activity_byte_raw"] = activity_byte
result["light_cycle_byte_raw"] = light_byte
# Keep this as logical, unescaped frame length for parser consistency.
result["frame_length"] = len(frame)
```

For the unmapped bytes hash, collect all byte positions that are NOT
currently mapped (i.e., not in the set of known indexes), exclude the
transport trailer (CRC + end delimiter), and compute a short hash from
index+value pairs:

```python
import hashlib

_MAPPED_INDEXES = {
    0, 1, 2, 3, 4, 5, 6, 7, 8,  # signature
    9,                             # water temp
    12, 13, 14, 16, 17, 28,       # pump, ozone, heater, setpoint, light, activity
    19, 20, 21, 22, 23, 24, 25, 26,  # heat schedule
    29, 30, 31, 32, 33, 34, 35, 36,  # filter schedule
    53, 54, 55, 56, 57, 58,          # datetime
}
# All indexes above are logical-frame (post-unescape) indexes.

_TRAILER_LEN = 5  # CRC32 (4) + frame end delimiter (1)
payload_end = max(0, len(frame) - _TRAILER_LEN)

digest_input = bytearray()
for i in range(payload_end):
    if i in _MAPPED_INDEXES:
        continue
    digest_input.extend((i & 0xFF, frame[i]))

result["unmapped_bytes_hash"] = hashlib.md5(bytes(digest_input)).hexdigest()[:8]
```

`unmapped_bytes_hash` is expected to be a lowercase 8-char hex string.

### Step 2: Add entity descriptions in `p25b85.py`

Append to the `_P25B85_ENTITIES` list:

```python
# Diagnostic sensors — raw byte values for protocol debugging
SpaEntityDescription(
    platform="sensor",
    key="heater_byte_raw",
    name="Heater byte (raw)",
    icon="mdi:memory",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="pump_byte_raw",
    name="Pump byte (raw)",
    icon="mdi:memory",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="ozone_mode_byte_raw",
    name="Ozone mode byte (raw)",
    icon="mdi:memory",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="activity_byte_raw",
    name="Activity byte (raw)",
    icon="mdi:memory",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="light_cycle_byte_raw",
    name="Light/cycle byte (raw)",
    icon="mdi:memory",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="frame_length",
    name="Frame length",
    icon="mdi:ruler",
    state_class="measurement",
    native_unit="bytes",
    entity_category="diagnostic",
    enabled_by_default=False,
),
SpaEntityDescription(
    platform="sensor",
    key="unmapped_bytes_hash",
    name="Unmapped bytes hash",
    icon="mdi:fingerprint",
    entity_category="diagnostic",
    enabled_by_default=False,
),
```

### Step 3: Update `sensor.py` — hex formatting for raw byte sensors

Raw byte sensors should display as hex strings (e.g., `0x55`) rather than
plain integers. Add a formatting check in `JoyonwaySensor.native_value`:

```python
_RAW_BYTE_KEYS = {"heater_byte_raw", "pump_byte_raw", "ozone_mode_byte_raw",
                  "activity_byte_raw", "light_cycle_byte_raw"}

@property
def native_value(self):
    if self.coordinator.data:
        value = self.coordinator.data.get(self._key)
        if value is not None and self._key in _RAW_BYTE_KEYS:
            return f"0x{value:02X}"
        return value
    return None
```

Since these are hex-formatted strings, they should NOT have a
`device_class` set (no temperature/enum). The entity descriptions above
already omit `device_class` for these sensors.

Also ensure `sensor.py` applies `description.native_unit` for non-temperature
sensors so `frame_length` correctly exposes `bytes`.

### Step 4: Add translations

Add entries to `strings.json` and all translation files (`en.json`,
`de.json`, `fr.json`) for the new entity keys.

English example (`strings.json` / `en.json`):
```json
"heater_byte_raw": { "name": "Heater byte (raw)" },
"pump_byte_raw": { "name": "Pump byte (raw)" },
"ozone_mode_byte_raw": { "name": "Ozone mode byte (raw)" },
"activity_byte_raw": { "name": "Activity byte (raw)" },
"light_cycle_byte_raw": { "name": "Light/cycle byte (raw)" },
"frame_length": { "name": "Frame length" },
"unmapped_bytes_hash": { "name": "Unmapped bytes hash" }
```

### Step 5: Update tests

- **`test_p25b85_adapter.py`**: Verify `parse_status()` returns the new
  diagnostic keys with correct values from adapter test frames.
- In `test_p25b85_adapter.py`, add coverage for `unmapped_bytes_hash`:
  - hash stays stable when only mapped bytes change
  - hash changes when unmapped bytes change
- **`test_entities_runtime.py`**: Verify diagnostic sensor entities are
  created with `entity_category=diagnostic` and `enabled_by_default=False`.
- Add a new test: verify hex formatting of raw byte sensors (e.g.,
  `heater_byte_raw` value `0x55` when heater byte is `0x55`).
- Add a sensor runtime assertion that `frame_length` exposes unit `bytes`.

### Step 6: Capture tool for unmapped bytes (Phase B)

Create `tools/capture_unmapped_bytes.py`:
- Connect to the EW11 bridge
- Capture N broadcast frames
- For each frame, extract unmapped bytes using the same mapped-index set and
  trailer exclusion as `parse_status()` so tool output matches integration logic
- Report: which unmapped bytes are static vs. changing, value ranges,
  potential semantic groupings
- Output a summary table to help identify firmware/version fields

This tool is for developer use only (not shipped in the integration).

## 4. Design Decisions

- **All diagnostic entities disabled by default.** Users who need protocol
  debugging can enable them individually. No UI clutter for normal users.
- **Hex display for raw bytes.** `0x55` is more useful than `85` for
  protocol analysis. These sensors use string state (no device_class).
- **`unmapped_bytes_hash`** is a lightweight change-detection mechanism.
  If the hash changes, something in the unmapped bytes shifted, prompting
  investigation. The MD5 prefix is not security-critical — it's a
  fingerprint for change detection only. Hash input excludes trailer bytes
  and uses index+value pairs for better signal quality.
- **No write commands.** This is purely read-only diagnostic exposure.
  Zero risk to the controller.
- **`frame_length` sensor** helps detect if the controller ever sends
  shorter/longer frames (firmware updates, different operating modes).
- **`frame_length` definition:** logical, unescaped frame length (including
  delimiters), to stay consistent with parser indexing.
- **`native_unit="bytes"`** for frame_length uses HA's generic unit
  string — there is no built-in HA unit for byte count, so a plain
  string suffix is appropriate.

## 5. Future Expansion (post-Phase A/B)

Once unmapped bytes are analyzed via the capture tool:

1. **Firmware version sensor** — if a version string or build number is
   found in the broadcast frame, expose it as a diagnostic sensor and
   add it to `device_info()` as `sw_version`.
2. **Error code sensor** — if error/fault bytes are identified, expose
   as an enum sensor with known error codes.
3. **Uptime sensor** — if an uptime counter exists in the frame.
4. **Raw frame diagnostic service** — a `joyonway.get_raw_frame` service
   that returns the last full broadcast frame as hex, useful for remote
   debugging without needing direct bridge access.

## 6. Files Modified

| File | Change |
|------|--------|
| `adapters/p25b85.py` | Add diagnostic keys to `parse_status()` result; add entity descriptions |
| `sensor.py` | Hex formatting for raw byte sensors and apply generic `native_unit` |
| `strings.json` | New entity translations |
| `translations/en.json` | New entity translations |
| `translations/de.json` | New entity translations |
| `translations/fr.json` | New entity translations |
| `tests/test_p25b85_adapter.py` | Verify new diagnostic keys and hash behavior in parse output |
| `tests/test_entities_runtime.py` | Verify diagnostic entity creation + sensor formatting/unit behavior |
| `tools/capture_unmapped_bytes.py` | New tool for unmapped byte analysis |

## 7. Risk Assessment

- **Risk: None for controller.** All changes are read-only — no new
  commands, no write paths, no behavioral changes to existing entities.
- **Risk: Minimal for integration.** New entities are disabled by default
  and use `entity_category=diagnostic`. Existing entity behavior unchanged.
- **Risk: Test breakage.** Tests that assert on the exact keys returned
  by `parse_status()` or entity count will need updates. Manageable.

## 8. Estimated Effort

| Task | Estimate |
|------|----------|
| Step 1–2: parse_status + entity descriptions | 15 min |
| Step 3: sensor hex formatting + generic native_unit handling | 10 min |
| Step 4: translations | 10 min |
| Step 5: tests | 20 min |
| Step 6: capture tool | 20 min |
| **Total** | **~75 min** |

