# Joyonway P25B85 Integration — Repository Review

Scope: extensive review of the `joyonway_p25b85` custom integration at the
current state described in `docs/plan.md` (post-session 23 polish). All
file references are relative to the repo root.

> TL;DR — The codebase is in **good shape** for a v0.1 pre-release. The
> architecture (persistent TCP reader, model adapter, intent queue with
> coalescing/serialization, optimistic UI with grace-window availability)
> is well thought out and consistently applied. A handful of concrete issues
> should be fixed before pushing a HACS release: `iot_class` mismatch,
> deprecated `OptionsFlowWithConfigEntry`, the duplicated coordinator
> storage (`hass.data` + `entry.runtime_data`), missing
> `config_entry=entry` on `DataUpdateCoordinator.__init__`, and a few
> small parser/lifecycle smells listed below. Tests are extensive (~120
> passing) but skewed toward adapter/CRC; a few coordinator edge cases
> (reconnect storms, buffer growth, intent queue races during shutdown)
> are under-covered.

---

## 1. Overall Architecture & Design Quality

| Aspect | Status | Notes |
|--------|--------|-------|
| Coordinator pattern | ✅ Good | Persistent TCP + background reader instead of polling; `DataUpdateCoordinator.async_set_updated_data()` used correctly. |
| Adapter abstraction | ✅ Good | `ModelAdapter` Protocol + `SpaEntityDescription` + registry. Ready for P23B32 / P69B133 additions; only P25B85 implemented today. |
| Intent queue | ✅ Strong | `IntentQueue` with coalesce window, no-op detection, sequential drain, retry, on_failure callbacks. Solves the “rapid toggle revert” class of bugs cleanly. |
| Optimistic state + grace | ✅ Good | Consistent pattern via `_SpaTargetStateSwitch` base + per-entity confirmation. Snap-back is non-silent (WARN logs). |
| Reconnect strategy | ✅ Good | Exponential backoff capped at 30s; stale-RX → reconnect; lock-guarded against concurrent attempts. |
| Layering | ⚠ Minor | `adapters/p25b85.py` does lazy `from ..protocol import build_frame` to avoid circular imports — symptom of `protocol.py` and `adapters/` being coupled. Could move `build_frame` into a smaller `frame.py` to remove the lazy import. |
| Separation of concerns | ✅ Good | Protocol/framing/CRC isolated from coordinator and entities. |

The intent queue is the standout design decision and is implemented cleanly
(`coordinator.py:60-201`).

---

## 2. Code Quality

### Strengths
- Consistent style: type hints everywhere, `from __future__ import annotations`, docstrings on public symbols, module headers explain intent.
- Naming follows the plan’s convention (no `_state`/`_status` suffixes, bare nouns: `jets`, `blower`, `light`, `setpoint`).
- Single base entity class (`JoyonwayCoordinatorEntity`) propagating availability avoids drift.
- `_SpaTargetStateSwitch` factors out optimistic boilerplate (`switch.py:171-235`).

### Smells / repetition
1. **Optimistic-state boilerplate duplicated** across `switch.py` (light + base), `fan.py`, `time.py`, `climate.py`: `_arm_pending_timeout`, `_cancel_pending_timeout`, `_pending_timeout`, `async_will_remove_from_hass`. Despite `_SpaTargetStateSwitch` extracting it for bool switches, `fan.py`, `time.py`, `climate.py`, and `SpaLightSwitch` each re-implement the same five methods with only the type of `_pending_state` differing. → Extract a `PendingStateMixin[T]` (generic on the pending value type) in `entity.py`.
2. **Schedule build-fn duplicated** in `switch.py:499-540` and `time.py:147-198` with near-identical logic (required-keys check, no-op detection, payload assembly). → Move into `adapters/p25b85.py` as a helper `build_schedule_from_state(data, overrides, schedule_type, write_mode)`.
3. **Required-keys list** appears 4 times (validate + build for both schedule entities). Define `_SCHEDULE_REQUIRED_KEYS(prefix)` once.
4. `setpoint_f: int = 0x62` (98°F) is hard-coded as the embedded current-setpoint default in `_build_button_command` (`p25b85.py:331`). Protocol notes say the controller ignores it, but it would be tidier to populate it from current `coordinator.data["setpoint"]` to match panel behavior.
5. Dead/legacy: `HEATER_BLOWER = 0x48`, `HEATER_HEATING_ALT = 0x54` (used; OK), `MASK_UV = MASK_ACTIVITY`, `IDX_UV_FLAG = IDX_ACTIVITY_FLAG` — legacy aliases kept “for backward compat” in a pre-release integration with no users. Recommend removing.
6. `pyproject.toml` `[tool.setuptools] packages = []` is a smell — nothing is actually packaged. Either drop the build-system stanza (use pure dev extras) or set `packages = ["custom_components.joyonway_p25b85"]`. The accidental `ha_joyonway_p25b85.egg-info/` in the workspace suggests the current setup confuses editable installs.
7. Mixed Sphinx-ish docstrings vs plain prose — minor; pick one.
8. `entity.py:11` returns a `dict` instead of `DeviceInfo` — works but typed `DeviceInfo` is preferred.

---

## 3. Correctness & Potential Bugs

### Probable issues (worth fixing)

1. **`DataUpdateCoordinator.__init__` missing `config_entry=entry`** (`coordinator.py:211-217`). Newer HA cores (>= 2024.x progressively, mandatory soon) require/recommend passing `config_entry`. Without it some bookkeeping (e.g. config-entry-scoped logger context) doesn’t link.

2. **Duplicate coordinator storage.** Both `entry.runtime_data = coordinator` (`__init__.py:26`) and `hass.data[DOMAIN][entry.entry_id] = coordinator` (`__init__.py:31`). Platforms read from `hass.data` (sensor/switch/etc.), `_async_options_updated` reads from `runtime_data`. Pick one (modern HA encourages `runtime_data` exclusively) to avoid drift if one is ever forgotten on unload.

3. **`iot_class: local_polling`** in `manifest.json:9` is incorrect. The integration maintains a persistent TCP connection and consumes ~2 Hz broadcast frames pushed by the controller; the user has no control over update cadence. Should be `local_push`.

4. **`OptionsFlowWithConfigEntry` is deprecated** (HA 2024.x). `config_flow.py:15,98-99`. Use plain `OptionsFlow` and access `self.config_entry` directly. Also drop the explicit `__init__(config_entry)` parameter on the subclass (deprecated since 2024.12).

5. **`_try_parse_buffer` drops broadcasts** (`coordinator.py:393-419`). Returns at the first valid broadcast and consumes through the last found frame’s end. If the buffer contains [A, B, C] where A is parseable and B,C are also parseable broadcasts, B and C are silently discarded. At 2 Hz this is OK in practice (only one frame arrives per read), but if the EW11 batches data or HA is slow, you lose updates. Suggest looping until exhausted and emitting the *latest* parsed dict.

6. **`buf.rfind(last_frame)` is fragile** (`coordinator.py:401`). If the last broadcast bytes happen to repeat earlier in the buffer (rare but not impossible — broadcasts are highly redundant), `rfind` may return the wrong position. Safer to track frame end indexes in `find_frames`.

7. **`Exception` swallowing in adapter parse** (`coordinator.py:412-415`). `Exception` is too broad and obscures real bugs. Narrow to `(IndexError, ValueError, KeyError)`, or at least keep `_LOGGER.exception` and re-raise during tests.

8. **`_async_update_data` may schedule reconnect twice** if both stale-RX and a parallel `_reader_loop` finally clause fire near-simultaneously. `_schedule_reconnect` guards via `_reconnect_task.done()`, so a second call early after the first is dropped — good — but the `await self._connect()` at line 465 then runs while a reconnect task is also queued. Consider: if `_reconnect_task` already exists/pending, skip the direct connect in `_async_update_data`.

9. **Light `_send_toggle` no-op path** (`switch.py:150-156`) checks `data.get("light") == overrides.get("light")`. Because `async_turn_on/off` already guard on `if not state` / `if state`, this guard is redundant but safe. However if the user calls the entity service rapidly while a previous toggle is pending (pending_state set but not yet confirmed), the second click is dropped by `_cmd_lock`; only if it slips through the lock would the no-op fire — fine.

10. **Climate `_pending_temp` timeout window** (`climate.py:174-216`). `_pending_temp` is set immediately in `async_set_temperature` but `_arm_pending_timeout()` only runs **after** the 1.5 s debounce + intent queue drain (`_debounced_send`). If broadcast happens to confirm during the debounce window, `_handle_coordinator_update` clears it correctly. But if `_debounced_send` is cancelled (rapid re-set) and never re-armed, the latest call’s `_arm_pending_timeout` runs only after the last successful drain. Edge case: if the queue’s `on_failure` fires before `_arm_pending_timeout` is reached, the `_on_failure` clears pending — OK.

11. **`_disconnect_ts` reset condition** (`coordinator.py:368`). On first data after reconnect, `_disconnect_ts = None`. But if the reader loop never receives data after reconnecting, `_disconnect_ts` stays at the previous disconnect time, so grace expiry is computed from a stale value. Minor — the grace window only matters once the connection was up; if we never get data after a reconnect, the entity should be unavailable, which is what happens.

12. **`_first_data_event` never reset on reconnect.** OK because `async_setup` awaits it only once; just worth a comment.

13. **`MASK_OZONE_MODE_MANUAL = 0x80`** at byte 13. Protocol doc says bit 7 ⇒ Manual. Code applies AND check correctly. But sensor sets `ozone_mode` value to `"manual"`/`"auto"` and **calls `hass.config_entries.async_update_entry`** from inside the reader loop on every broadcast where modes differ (`coordinator.py:472-485`). After the first divergence the option will already match; subsequent calls won’t fire — OK. But this write happens during frame parsing in the loop; safer to check `OPT_OZONE_MODE` against `entry.options` once after update so the loop doesn’t race with the user’s options flow submit.

14. **Schedule intent groups don’t cross-merge.** `heat_schedule_state` and `heat_schedule_time` are separate groups. If a user enables slot 1 and edits its time within 300 ms, two commands are sent (state-mode first, then time-mode). Both succeed and are idempotent, but you could collapse them by promoting any time-mode pending into the state command (or vice versa). Low priority — current behavior is correct, just sub-optimal.

15. **Climate exposes only `HVACMode.HEAT`** (`climate.py:55`). No `HVACMode.OFF` means HA users can’t "turn off" the climate to disable heating — they must use the heater switch. Consider adding `HVACMode.OFF` mapped to a heater-off command for UX, or document that the heater switch is the canonical heater control.

16. **Live tests aren’t markered** (`tests/live/`). They’re skipped via filesystem discovery only; if someone runs `pytest tests/` they may hit live tests requiring real hardware. Add `pytest.mark.live` and `--strict-markers` skip-by-default.

### Non-issues / verified-OK
- CRC implementation matches `docs/protocol.md` and is verified by `tests/test_p25b85_adapter.py::test_build_schedule_command_phase6_match` and friends.
- Frame escape/unescape is symmetric (`pseudo_escape` / `pseudo_unescape`) and tested in `tests/test_frame_protocol.py`.
- `_write_lock` correctly serializes writes; `COMMAND_COOLDOWN=1.0s` enforced post-lock.
- Shutdown sequencing in `async_unload_entry` is correct (platforms unload first, then `async_shutdown()`).

---

## 4. Home Assistant Best Practices

| Area | Status | Notes |
|------|--------|-------|
| `manifest.json` | ⚠ | `iot_class` wrong (see §3.3); `dependencies` empty is fine; missing `quality_scale` (not required for HACS custom). |
| `config_flow.py` | ⚠ | Uses deprecated `OptionsFlowWithConfigEntry`; otherwise solid (unique_id, abort-on-duplicate, TCP test). The TCP test doesn’t verify it’s a Joyonway bridge — accepts any open TCP port. Optional: read a few bytes, look for `0x1A ... 0x1D` framing. |
| Translations | ✅ | `strings.json` + `en/de/fr` all present and same key-set. `strings.json` is byte-identical to `en.json`. |
| `unique_id` strategy | ✅ | All entities derive `{entry.entry_id}_{key}`. |
| `device_info` | ✅ | Single source via `entity.py:device_info`. Consider typing the return as `DeviceInfo`. |
| Entity availability | ✅ | Grace window centralized in coordinator; `JoyonwayCoordinatorEntity` propagates. `bridge_connectivity` correctly overrides to stay available always. |
| `diagnostics` platform | ❌ | None implemented. Recommended (anonymize host) — easy win for support requests. |
| `services.yaml` | n/a | No custom services exposed (good — uses platforms idiomatically). |
| `repairs` issues | n/a | Not used; fine. |
| `entry.runtime_data` typing | ⚠ | Untyped; could `entry: ConfigEntry[JoyonwayP25B85Coordinator]` in newer HA. |
| Logger | ✅ | Per-module loggers; consistent format. |
| Strict unload | ✅ | Implemented correctly. |
| Adapter pattern for entities | ✅ | Sensor/binary_sensor driven by `entity_descriptions()`. Switches/fans/climate/time are hand-coded — appropriate, since they need write behaviour. |

---

## 5. Protocol Implementation vs `docs/protocol.md`

Cross-checked `adapters/p25b85.py` against `docs/protocol.md`:

| Topic | Doc says | Code does | Match |
|-------|----------|-----------|-------|
| Frame delimiters | `0x1A` / `0x1D` / `0x1B` | `protocol.py:11-13` | ✅ |
| Escape table | 5 entries | `protocol.py:16-22` | ✅ |
| CRC polynomial | `0x04C11DB7`, xor_out `0x552D22C8`, word32-swap | `protocol.py:28-30,135-155` | ✅ |
| Broadcast bytes 9/12/13/14/16/17 | per doc | `IDX_*` constants | ✅ |
| Byte 28 bit 3 = blower, bit 5 = activity | per doc | `MASK_BLOWER=0x08`, `MASK_ACTIVITY=0x20` | ✅ |
| Byte 14 status map (0x40/0x50/0x51/0x54/0x55/0x41/0xC1/0x48/0x58) | per doc | `HEATER_STATE_MAP` + `MASK_HEATER_BLOWER` strip | ✅ — blower stripped before lookup is correct |
| Heating cycle flag byte 17 bit 7 | per doc | `MASK_HEATING_CYCLE=0x80` + post-heat circulation detection | ✅ |
| Schedule flags state/time tables | per doc (§4.3) | `SCHED_FLAGS_STATE_TABLE` / `SCHED_FLAGS_TIME_WRITE_TABLE` | ✅ |
| Temperature btn_action `0x98` | per doc | `build_temp_command` | ✅ |
| Ozone mode context byte `0xC0`/`0x40` + modifier `0x80` | per doc | `build_ozone_mode_command` | ✅ |
| Ozone manual ON `0x01`/OFF `0x10`, btn_group `0x01`, context `0x40` | per doc | `build_ozone_manual_command` | ✅ |
| Schedule enable bit `0x40` in start-hour | per doc | `MASK_SLOT_ENABLED=0x40` | ✅ |
| DateTime prefix `0x05` (date+time) / `0x50` (time only) | per doc | `build_datetime_command(set_date=...)` | ✅ |

**No discrepancies found** between doc and code.

Other observations:
- Schedule parser correctly masks the hour with `MASK_SLOT_HOUR = 0x3F` before exposing the value, so the enable bit doesn’t leak into the displayed hour.
- `_fahrenheit_to_celsius` treats `0` and `> 200` as invalid → `None`. Good defensive parse.
- The frame signature `P25B85_SIGNATURE` checks 9 bytes including model byte `0x03`. This correctly rejects P23B32 (`0x02`) broadcasts.

---

## 6. Testing

### Coverage by area (qualitative)

| Area | File(s) | Coverage |
|------|---------|----------|
| Frame protocol / escape / CRC | `test_frame_protocol.py`, `test_p25b85_adapter.py` | ✅ Strong (golden-frame matches, edge cases). |
| Adapter parse & command build | `test_p25b85_adapter.py` (622 lines) | ✅ Comprehensive across all command types. |
| Coordinator lifecycle / intent queue | `test_coordinator_resilient.py` (477 lines) | ✅ Good (shutdown, reconnect, optimistic timeout, flush, build error). |
| Entity runtime | `test_entities_runtime.py` | ✅ Decent (light unknown, schedule missing data, optimistic, climate debounce). |
| `__init__` / options flow | `test_init_runtime.py` (101 lines) | ⚠ Only options-update flush ordering. No `async_setup_entry`/`async_unload_entry` happy/failure paths. |
| Config flow | — | ❌ Not tested (no `test_config_flow.py`). |
| Diagnostics | — | n/a (not implemented). |

### Gaps
- **`config_flow.py`** has zero direct unit tests (unique_id abort, cannot_connect error, options flow show + submit).
- **Reconnect storm / backoff progression** not asserted (only single-reconnect tests).
- **Buffer overflow path** (`len(buf) > 8192` truncation) not tested.
- **`_try_parse_buffer` with multiple broadcasts in one chunk** not tested — would catch the “drops intermediate frames” bug listed in §3.5.
- **`async_unload_entry`** not directly tested.
- **Stale-RX → reconnect path** has a test but it uses an injected stale timestamp; happy-path of receiving fresh data after stale isn’t covered.

---

## 7. Security & Safety

- ✅ No automatic writes on startup (verified — only options-flow ozone sync and explicit user actions).
- ✅ Schedule writes require complete prerequisites; raise `HomeAssistantError`/`IntentBuildError` rather than silently writing zeros (`switch.py:474-491`, `time.py:218-235`).
- ✅ Write pacing (1 s cooldown, single in-flight via lock) — bus contention impossible.
- ✅ CRC is computed dynamically; no replay frames.
- ✅ No credentials. Only host:port stored.
- ⚠ TCP open check accepts any open port — does not verify Joyonway protocol. Low risk but a benign typo could attach to e.g. an MQTT broker.
- ⚠ `configuration_url = http://{host}` (`entity.py:19`) assumes the EW11 web UI is on port 80; harmless if absent.
- ⚠ Reconnect on `OSError` is broad — guards against connection refused but also masks programming errors. Adding a small jitter to the backoff and a per-attempt log line at INFO+ already done. Good.

---

## 8. Documentation

| Doc | State | Notes |
|-----|-------|-------|
| `README.md` | ✅ Strong | Clear, consistent with implementation. Lists all entities accurately. |
| `docs/protocol.md` | ✅ Strong | Canonical, current with code. |
| `docs/plan.md` | ✅ Working journal | Session 23 last entry; consistent with code. |
| `docs/live_test_plan.md` | not reviewed | exists, presumably current |
| `tests/README.md` | exists | not reviewed in depth |
| Inline docstrings | ✅ Good | Module headers + public-symbol docstrings |
| HACS rendering | ✅ `hacs.json` `render_readme: true` |

Minor:
- README references “version bump and release” as next step — consistent with plan.
- README mentions HA 2024.1+ minimum. The deprecated `OptionsFlowWithConfigEntry` was deprecated in 2024.12; works on 2024.1 fine but will warn on newer versions.
- The README claims “Ozone control not yet live-tested” under “What this integration does NOT do”; align with plan.md Priority 2 list.

---

## 9. Release Readiness (HACS v0.1)

Must-fix before release:
1. Fix `iot_class` → `local_push` (`manifest.json`).
2. Migrate off `OptionsFlowWithConfigEntry` (deprecation warning surfaces in HA UI logs).
3. Add `config_entry=entry` to `DataUpdateCoordinator.__init__`.
4. Decide on one of `hass.data[DOMAIN][entry_id]` vs `entry.runtime_data` and remove the other.
5. Add basic config-flow tests (cannot_connect, duplicate abort, happy path) — needed for any kind of CI confidence.

Should-have:
6. Add `diagnostics.py` (host, options, redacted entry data, last broadcast hex).
7. Add `pytest` marker for live tests; default-skip in CI.
8. Live ozone test (still pending per plan).
9. Tighten `_try_parse_buffer` to drain all broadcasts in the chunk and pick the latest.

Nice to have:
10. Capability-driven blower entity (already a planned item).
11. Hardware section in options flow for blower presence.
12. Merge `heat_schedule_state` + `heat_schedule_time` intent groups (only an optimization).
13. Generic `PendingStateMixin` to dedupe optimistic boilerplate.
14. Drop legacy aliases (`MASK_UV`, `IDX_UV_FLAG`, `HEATER_BLOWER`) — pre-release, no compat constraint.
15. Type `device_info()` return as `DeviceInfo`.
16. Fix `pyproject.toml` packages list or document why it’s empty.

---

## 10. Prioritized Recommendations

### Must fix before v0.1 release
| # | File | Change |
|---|------|--------|
| M1 | `manifest.json:9` | `iot_class: local_polling` → `local_push`. |
| M2 | `config_flow.py:15,98-117` | Replace `OptionsFlowWithConfigEntry` with `OptionsFlow`; remove the explicit `__init__(config_entry)` override; use `self.config_entry`. |
| M3 | `coordinator.py:211-217` | Pass `config_entry=entry` to `super().__init__(...)`. |
| M4 | `__init__.py:26,30-31` and all platforms | Pick one storage (recommend `entry.runtime_data`) and remove the other. Update `sensor.py`, `binary_sensor.py`, `switch.py`, `fan.py`, `climate.py`, `time.py`, `button.py` accordingly. |
| M5 | `tests/` | Add `tests/test_config_flow.py` covering: happy path, cannot_connect, duplicate unique_id, options-flow show + submit. |

### Should improve
| # | File | Change |
|---|------|--------|
| S1 | `coordinator.py:393-419` | Loop over all broadcasts in the chunk, return latest parsed dict. Replace `buf.rfind` with explicit indexes returned from `find_frames`. |
| S2 | `coordinator.py:412-415` | Narrow `except Exception` to specific exceptions for adapter parse. |
| S3 | `coordinator.py:472-485` | Hoist `_sync_ozone_mode` / `_check_clock_drift` out of the parsing hot path or rate-limit; they currently run on every broadcast. |
| S4 | New file `diagnostics.py` | Implement `async_get_config_entry_diagnostics` (host, port, options, last data redacted). |
| S5 | `tests/live/` | Add `pytest.mark.live` marker and document opt-in. |
| S6 | `climate.py:55` | Consider adding `HVACMode.OFF` for natural HA semantics, mapped to heater-off command. |

### Nice to have
| # | File | Change |
|---|------|--------|
| N1 | `entity.py` | Add `PendingStateMixin[T]` to dedupe pending-state logic across `light`, `fan`, `time`, `climate`. |
| N2 | `adapters/p25b85.py` | Add `build_schedule_from_state(data, overrides, schedule_type, write_mode)` to remove duplicated build-fn logic between `switch.py` and `time.py`. |
| N3 | `adapters/p25b85.py` | Drop legacy aliases (`MASK_UV`, `IDX_UV_FLAG`, `HEATER_BLOWER`, `HEATER_HEATING_ALT` if not used). |
| N4 | `entity.py:11-20` | Return `DeviceInfo(...)` instead of `dict`. |
| N5 | `coordinator.py` | Coalesce `heat_schedule_state`/`heat_schedule_time` so an enable + time edit becomes a single time-mode write. |
| N6 | `pyproject.toml:18-19` | Fix or drop the empty `packages` list; the integration is loaded via HA path, not pip. |
| N7 | `config_flow.py:42-53` | After TCP open, read a small window and look for `0x1A ... 0x1D` framing to confirm bridge type. |
| N8 | `hacs.json` | Add `"homeassistant": "2024.1.0"` minimum version. |

---

## Appendix A — File/symbol map (quick reference)

```
custom_components/joyonway_p25b85/
├── __init__.py          (77)   setup/unload, options listener; submits ozone-mode intent
├── const.py             (47)   domain, timing, options keys, PLATFORMS
├── manifest.json        (15)   iot_class needs fix
├── config_flow.py       (124)  uses deprecated OptionsFlowWithConfigEntry
├── protocol.py          (169)  frames, escape, CRC32 (verified)
├── coordinator.py       (544)  persistent TCP, IntentQueue (60-201), grace mode
├── entity.py            (30)   device_info, JoyonwayCoordinatorEntity
├── sensor.py            (97)   adapter-driven
├── binary_sensor.py     (102)  adapter-driven + bridge connectivity
├── switch.py            (552)  light, heater, blower, ozone, schedule enables
├── fan.py               (188)  jets off/low/high
├── climate.py           (249)  thermostat with debounce
├── time.py              (237)  schedule slot times
├── button.py            (88)   sync clock
├── strings.json         (105)  == translations/en.json
├── adapters/
│   ├── __init__.py      (23)   registry
│   ├── base.py          (50)   Protocol + SpaEntityDescription
│   └── p25b85.py        (594)  byte map, parse_status, all builders
└── translations/        en/de/fr (104 lines each, parity OK)
```

## Appendix B — Quick risk matrix

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Lost broadcasts when multiple frames in one TCP chunk | Medium | Low (next broadcast is ~500 ms away) | S1 |
| Deprecation warning surfaces on HA 2024.12+ | High | Low | M2 |
| Schedule write with stale data overwrites valid times with zeros | Very Low (guarded) | High | Already guarded — keep tests. |
| Reader exception silently drops state updates | Low | Medium | S2 |
| Options flow + ozone broadcast race writes wrong mode | Low | Low | S3 |
| EW11 TCP socket exhaustion | Low (4-client cap, HA uses 1) | Low | Documented in README |

