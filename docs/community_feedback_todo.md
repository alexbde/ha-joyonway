# Community Feedback TODO — KDy & old-man testing (2026-05-28)

Source: https://community.home-assistant.io/t/joyonway-spa-control/582344/

## 🚨 CRITICAL: Factory reset required (KDy, post #134)

KDy reports the integration **completely changed his spa configuration**:
- Frames on the bus changed (configuration overwritten)
- Couldn't restore from panel (ozone stuck on "A" / Auto)
- Power cycle did NOT fix it
- **Only a factory reset worked**

### Root cause analysis needed:
1. **Ozone mode command on startup?** — If the options flow default is "Auto"
   and we send a mode command on integration load/reload, it would persistently
   override the panel's setting. Need to verify: does anything send ozone mode
   commands automatically?
2. **Schedule overwrite on enable toggle** — The `_send_schedule()` method reads
   current values from `coordinator.data` and re-sends the full schedule. If data
   is stale/None/default, it sends zeros or wrong values, corrupting the schedule.
3. **Auto clock sync** — If enabled by default, this sends writes on startup.
   Combined with other commands, could confuse the controller.

### Fix needed:
- [x] **Never send write commands automatically on startup/reload**. All writes
      should be user-initiated only. Removed `async_apply_ozone_mode()` call from
      `__init__.py` and the method from `coordinator.py`.
- [x] Remove or disable auto clock sync by default (changed default to False;
      users must explicitly enable it in options flow).
- [x] Ozone mode should NEVER be sent unless the user explicitly toggles it in HA.
      The options flow now only controls entity visibility, not send commands.

---

## ❌ Jets: Can switch ON, cannot switch OFF (KDy, post #132)

KDy can turn jets ON but OFF doesn't work.

### Possible causes:
1. **Different controller firmware variant** — KDy might have P23B32 or a P25B85
   firmware revision where the pump OFF command bytes differ.
2. **State tracking issue** — our fan entity uses `_PUMP_TRANSITIONS` based on
   _current_ state. If the state read is wrong (e.g., "low" when it's "high"),
   we'd send the wrong transition.
3. **Retry timing** — high→off needed a retry on our spa too (RS485 collision).
   If KDy's bus is noisier, 3 retries might not be enough.

### Fix needed:
- [ ] Verify: does our integration correctly detect pump state on KDy's spa?
      (He says statuses work, so likely yes)
- [x] Add logging when jets OFF command is sent to show which transition was used
- [x] Consider: allow direct "off" command (0x04, 0x00) regardless of detected
      current state, as a fallback. Implemented: alternate OFF command tried after
      retries exhausted, plus fallback when state is unknown.

---

## ❌ Filter slot 1/2 switches toggle filtration ON/OFF (KDy, post #132)

The enable/disable switches for filter schedule slots are directly toggling
the filtration pump instead of just enabling/disabling the schedule slot.

### Root cause:
The `_send_schedule()` method sends a FULL 0xA4 schedule command with all slot
times + enable flags. On KDy's spa, this command appears to immediately activate
filtration (as if the controller interprets the command as "run this schedule now"
rather than "set this schedule for later").

### Possible causes:
1. **Slot time contains current hour** — if the schedule slot happens to contain
   the current time, writing it "re-arms" it and the controller immediately starts.
2. **Enable flag interpretation** — maybe KDy's firmware treats `slot_enabled=true`
   as "start now" rather than "enable for future scheduled start."
3. **Different flags byte encoding** — KDy's controller might use different values.

### Fix needed:
- [x] Add a guard: don't send schedule commands if we're unsure about the values.
      Both `switch.py` and `time.py` now refuse to send if any required key is
      missing from coordinator data.
- [ ] Consider making schedule writes a "confirm" action rather than a simple toggle
- [ ] Ask KDy: does this happen even with schedule times far in the future?

---

## ❌ Heat slot 1/2: Does not activate at set time (KDy, post #132)

KDy says heat schedule slots don't work — they don't trigger heating at the
programmed time.

### Possible causes:
1. **We're disabling them** — if our default state has `slot_enabled=False` and
   we send that on startup, we'd disable his heat schedule.
2. **Wrong enable flags** — `SCHED_FLAGS_TABLE` might be different for his firmware.
3. **Schedule overwrite** — same as the filtration issue above.

### Fix needed:
- [x] Same as above: never auto-send schedule commands.
- [ ] Verify the enable flags work correctly on KDy's hardware.

---

## ❌ Filter slot 1: Incorrectly overwrites start time (KDy, post #132)

When toggling the filter slot enable, the start time gets overwritten.

### Root cause (likely):
In `_send_schedule()`, we read `data.get(f"{prefix}_slot1_start", (0, 0))`.
If the coordinator hasn't received a broadcast yet (or data is stale from before
the last schedule change), we'd send stale/default values, overwriting the real
schedule times.

### Fix needed:
- [x] Add a guard: refuse to send schedule if `coordinator.data` is None or missing
      the required keys (raise an error instead of sending defaults)
- [x] Remove the `(0, 0)` fallback in `_send_schedule()` — if data is missing,
      fail loudly rather than silently sending zeros
- [ ] Force a fresh broadcast read before building the schedule command

---

## ❌ Ozone: Inactive / button does nothing (KDy, post #132)

Ozone switch doesn't work for KDy.

### Possible causes:
1. **Options flow default = Auto** — ozone switch is unavailable (grayed out) in
   Auto mode. KDy may not have changed the option to "Manual."
2. **Two-step process fails** — our ON sequence sends mode→Manual, delay, then
   manual ON. If mode switch fails silently, the manual ON also fails.
3. **Different command bytes** — KDy's firmware may use different ozone commands.

### Fix needed:
- [ ] Improve UX: show a warning when ozone switch is in "auto" mode (not just
      gray it out)
- [x] Add error logging for each step of the two-step ozone process

---

## ⚠️ No pump speed shown during heating/filtering (old-man, post #130)

When heating/filtering is active, old-man doesn't see pump speed.

### Possible cause:
The pump byte (byte 12) might show 0x00 when the controller runs the pump
internally for heating/filtering (as opposed to user-triggered jets). The pump
status sensors only check `pump_low`/`pump_high` from byte 12.

### Fix needed:
- [ ] Consider: derive "circulation" state from heater_byte (0x50 = circulation)
      and show it in the jets sensor as a 4th state, or add a separate "pump
      active" binary sensor.

---

## Priority order for fixes:

1. **🚨 Safety: Never auto-send commands on startup** ✅ FIXED
2. **Schedule overwrite guard** ✅ FIXED (refuse to send with missing data)
3. **Jets OFF reliability** ✅ FIXED (fallback + logging added)
4. **Ozone UX** (partially done — logging added, UI warning still TODO)
5. **Pump state during heating** (cosmetic improvement)

---

## Notes:
- KDy has 28 posts in the thread and runs his own Python script too. He might
  have a P23B32 variant that reports the same broadcast signature as P25B85.
- old-man has a different bridge (USR-W610 instead of EW11) but same controller.
- Both users have the integration *partially* working — reads are fine, writes
  have issues.
- The most critical finding is that our integration CAN CORRUPT spa settings
  requiring a factory reset. This MUST be fixed before any release.

