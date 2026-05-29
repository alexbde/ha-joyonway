# Resilient UI — Implementation Plan

> **Goal:** Replace the polling coordinator with a persistent TCP connection and
> add optimistic state to all writable entities, so the HA dashboard gives
> instant visual feedback and real-time state updates.
>
> **Context:** Read `docs/plan.md` first for project overview and conventions.
> Read `docs/protocol.md` for the RS485 framing/CRC details.

---

## 1. Problem Statement

The current coordinator (`coordinator.py`) polls the RS485 bridge every 30 s:
each cycle opens a TCP connection, reads until a broadcast frame arrives,
parses it, and closes the connection. Commands open a *second* short-lived
connection, write the frame, close, then trigger another poll to confirm.

This causes three UX problems:

1. **No UI feedback on click.** Writable entities (switches, fan, etc.) send
   the command and then `await coordinator.async_request_refresh()`. Until
   that refresh completes (2–5 s), the Lovelace icon shows no change at all —
   the user thinks the click didn't register.
2. **Stale state.** Between polls the UI is up to 30 s behind. The spa
   broadcasts status every ~1–2 s on the RS485 bus, but we ignore it.
3. **Hardcoded sleeps.** `asyncio.sleep(1.0)` after light toggle, `0.1 s`
   after every command send — all "hope the controller processed it" delays
   that should be replaced by waiting for the next broadcast confirmation.

---

## 2. Architecture Change: Persistent Connection

### 2.1 Current flow (polling)

```
Every 30 s:  connect → read → parse → disconnect
Command:     connect → write → disconnect → connect → read → disconnect
```

### 2.2 New flow (persistent, single connection)

```
Startup:     connect, launch background reader task
Reader:      continuously read TCP stream → find_frames → parse → update data
Command:     acquire write lock → write to same socket → release lock
Reconnect:   on any error, reconnect with exponential backoff
```

Key properties:

- **Single TCP connection** — required to support bridges that allow only one
  connection (e.g., some non-EW11 bridges). The EW11 supports 4, but we must
  not assume that.
- **Shared socket for reads and writes** — the reader task continuously
  consumes incoming bytes; command writes are serialized with an `asyncio.Lock`
  so a write happens between reads. No read/write collision possible because
  TCP is full-duplex; the lock only prevents two commands from interleaving.
- **State updates in ~1–2 s** — each broadcast frame the spa sends is parsed
  and pushed to entities immediately, instead of waiting for the next poll.

### 2.3a Decisions Applied (from open questions)

These choices are now fixed for implementation:

- **Unload is strict:** unload/shutdown must leave zero background tasks.
- **Availability uses grace mode:** keep entities available briefly to avoid flicker.
- **Optimistic state has timeout:** pending state expires if no confirmation arrives.
- **Disconnected command path schedules reconnect:** fail fast + start recovery.
- **Startup wait is bounded:** wait briefly for first frame, never indefinitely.

Default timing values for this plan:

- `AVAILABILITY_GRACE_SECONDS = 10`
- `RX_STALE_SECONDS = 15`
- `OPTIMISTIC_TIMEOUT_SECONDS = 10`

Time source rule:

- Use `time.monotonic()` for elapsed-time checks (`_last_rx_ts`, `_disconnect_ts`,
  command cooldown), including inside sync properties like `available`.
- Do not call `asyncio.get_running_loop().time()` from sync properties.

Implementation note: define these in `const.py` and import from there in
`coordinator.py` and entity files. Do not inline magic numbers.

Protocol alignment note:

- `docs/protocol.md` is canonical for bus semantics and must stay consistent
  with this plan. For jets/pump control, this plan assumes controller-accepted
  target bytes (`off/low/high`) with one bounded retry on mismatch confirmation.

### 2.3 Coordinator changes (`coordinator.py`)

Replace the current `DataUpdateCoordinator` polling model with a hybrid:

Required imports for the snippets in this section:

```python
import asyncio
import contextlib
import time
```

```python
class JoyonwayP25B85Coordinator(DataUpdateCoordinator):
    """Coordinator with persistent TCP connection and background reader."""

    def __init__(self, hass, host, port, model, entry):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            # Fallback poll interval — only used if the persistent connection
            # dies silently (no data for N seconds). Acts as a health check.
            update_interval=timedelta(seconds=60),
        )
        self.host = host
        self.port = port
        self.model = model
        self.entry = entry
        self._adapter = get_adapter(model)
        self._available = False

        # Persistent connection state
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._connect_lock = asyncio.Lock()      # serialize connect attempts
        self._reconnect_task: asyncio.Task | None = None
        self._reconnect_delay = 1.0  # exponential backoff
        self._stopped = False         # set True during unload/shutdown
        self._last_rx_ts: float = 0.0
        self._disconnect_ts: float | None = None
        self._first_data_event = asyncio.Event()

        # Command pacing (keep the 1.0 s cooldown between writes)
        self._last_command_ts: float = 0.0

        # Existing fields (ozone sync, clock sync) stay unchanged
```

#### New methods

**`async_setup()` — called once from `__init__.py` after coordinator creation:**

```python
async def async_setup(self) -> None:
    """Establish the persistent connection and start the reader."""
    await self._connect()
    # Avoid setup race: wait briefly for first parsed broadcast.
    # If timeout occurs, first_refresh still decides availability.
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(self._first_data_event.wait(), timeout=TCP_TIMEOUT)
```

**`_connect()` — open TCP + launch reader task:**

```python
async def _connect(self) -> None:
    """Open TCP connection and start the background reader task."""
    if self._stopped:
        return
    async with self._connect_lock:
        if self._stopped or self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=TCP_TIMEOUT,
            )
            self._available = True
            self._disconnect_ts = None
            self._reconnect_delay = 1.0  # reset backoff
            self._reader_task = self.hass.async_create_task(self._reader_loop())
            _LOGGER.info("RS485 bridge connected: %s:%s", self.host, self.port)
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("RS485 bridge connection failed: %s", err)
            self._available = False
            self._schedule_reconnect()
```

**`_reader_loop()` — background task that continuously parses broadcasts:**

```python
async def _reader_loop(self) -> None:
    """Read TCP stream and parse broadcast frames continuously."""
    reader = self._reader
    if reader is None:
        return
    buf = bytearray()
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                _LOGGER.warning("RS485 bridge disconnected (EOF)")
                break
            buf.extend(chunk)

            # Try to parse complete frames from the buffer
            result, consumed = self._try_parse_buffer(buf)
            if result is not None:
                self._last_rx_ts = time.monotonic()
                self._disconnect_ts = None
                self._first_data_event.set()
                # Push new data to all entities immediately
                self.async_set_updated_data(result)
            if consumed:
                del buf[:consumed]

            # Prevent unbounded growth
            if len(buf) > 8192:
                buf = buf[-2048:]
    except (OSError, asyncio.CancelledError):
        pass
    finally:
        self._disconnect_ts = time.monotonic()
        self._available = False
        await self._close_connection()
        if not self._stopped:
            self._schedule_reconnect()
```

Add grace-mode availability behavior:

```python
@property
def available(self) -> bool:
    """True while connected, or briefly after disconnect to avoid flicker."""
    if self._available:
        return True
    if self.data is None or self._disconnect_ts is None:
        return False
    now_ts = time.monotonic()
    return (now_ts - self._disconnect_ts) <= AVAILABILITY_GRACE_SECONDS
```

### 2.3b Availability propagation to entities (required)

To make grace mode visible in Home Assistant, entities must read availability
from coordinator grace logic consistently.

Implement one shared base class in `entity.py` (recommended):

```python
class JoyonwayCoordinatorEntity(CoordinatorEntity):
    @property
    def available(self) -> bool:
        return self.coordinator.available
```

Then have all platforms inherit this base (switch/fan/climate/time/button/sensor/binary_sensor).
Without this step, grace mode may not apply uniformly.

The key call is `self.async_set_updated_data(result)` — this is a built-in
`DataUpdateCoordinator` method that updates `self.data` and notifies all
`CoordinatorEntity` subscribers, triggering their `_handle_coordinator_update`.

**`_schedule_reconnect()` — exponential backoff:**

```python
def _schedule_reconnect(self) -> None:
    """Schedule a reconnection attempt with exponential backoff."""
    if self._stopped:
        return
    if self._reconnect_task is not None and not self._reconnect_task.done():
        return
    delay = self._reconnect_delay
    self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)
    _LOGGER.info("Reconnecting in %.0fs", delay)
    self._reconnect_task = self.hass.async_create_task(self._reconnect_after(delay))

async def _reconnect_after(self, delay: float) -> None:
    await asyncio.sleep(delay)
    if not self._stopped:
        await self._connect()
```

**`_close_connection()` — tear down cleanly:**

```python
async def _close_connection(self) -> None:
    """Close the TCP connection and cancel the reader task."""
    if self._writer is not None:
        writer = self._writer
        writer.close()
        self._writer = None
        with contextlib.suppress(Exception):
            await writer.wait_closed()
    self._reader = None
    if self._reader_task is not None and self._reader_task is not asyncio.current_task():
        self._reader_task.cancel()
        self._reader_task = None
```

**`async_send_command()` — write on the shared connection:**

```python
async def async_send_command(self, frame: bytes) -> bool:
    """Send a command frame on the persistent connection."""
    if self._writer is None:
        _LOGGER.error("Cannot send command: not connected")
        self._schedule_reconnect()
        return False

    async with self._write_lock:
        # Pacing: enforce cooldown between commands
        elapsed = time.monotonic() - self._last_command_ts
        if elapsed < COMMAND_COOLDOWN:
            await asyncio.sleep(COMMAND_COOLDOWN - elapsed)

        try:
            self._writer.write(frame)
            await self._writer.drain()
            self._last_command_ts = time.monotonic()
            _LOGGER.debug("Sent command: %s", frame.hex())
            return True
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.error("Command send failed: %s", err)
            await self._close_connection()
            self._schedule_reconnect()
            return False
```

No more `async_request_refresh()` after commands — the reader loop will
pick up the next broadcast (arriving in ~1–2 s) and push it automatically.

**`_async_update_data()` — fallback poll (health check only):**

```python
async def _async_update_data(self) -> dict:
    """Fallback: if persistent connection is alive, return cached data."""
    now_ts = time.monotonic()
    if self._available and self.data is not None:
        # Health check: if stream is stale, force reconnect.
        if now_ts - self._last_rx_ts > RX_STALE_SECONDS:
            _LOGGER.warning("No RX data for %.0fs, reconnecting", now_ts - self._last_rx_ts)
            await self._close_connection()
            self._available = False
            self._schedule_reconnect()
        else:
            return self.data
    # If not connected, try to reconnect (guarded by _connect_lock)
    if not self._available:
        await self._connect()
    if self.data is None:
        raise UpdateFailed("No data from RS485 bridge")
    return self.data
```

**`async_shutdown()` — called on HA stop / config entry unload:**

```python
async def async_shutdown(self) -> None:
    """Close connection on shutdown."""
    self._stopped = True
    if self._reconnect_task is not None:
        self._reconnect_task.cancel()
        self._reconnect_task = None
    await self._close_connection()
```

Also update parser contract to prevent duplicate re-processing:

```python
def _try_parse_buffer(self, buf: bytes) -> tuple[dict | None, int]:
    """Return (parsed_data, consumed_bytes)."""
    # consumed_bytes must advance past frames already scanned/parsed
```

#### `__init__.py` changes

After creating the coordinator, call `async_setup()`:

```python
coordinator = JoyonwayP25B85Coordinator(hass, host, port, model, entry)
await coordinator.async_setup()
entry.runtime_data = coordinator
# Then the first data will arrive from the reader loop within ~2 s
await coordinator.async_config_entry_first_refresh()
```

On unload, enforce strict shutdown before removing coordinator data:

```python
async def async_unload_entry(hass, entry):
    coordinator = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await coordinator.async_shutdown()
    else:
        # Entry remains loaded; coordinator must keep running.
        _LOGGER.warning("Unload failed; coordinator stays active")
    return unload_ok
```

Important: call `async_shutdown()` **only after** platform unload succeeds.
If unload fails, do not stop the coordinator.

Note: `async_config_entry_first_refresh()` may need adjustment — if the
persistent reader already populated `self.data` before the first poll fires,
the fallback `_async_update_data` just returns cached data. If not connected
yet, it raises `UpdateFailed` and HA shows the integration as unavailable
(correct behaviour).

### 2.4 Ozone sync and clock sync

These currently run inside `_async_update_data()`. Move them into the
reader loop's data-processing path — after `self._try_parse_buffer()`
returns a result, run the ozone mode check and clock drift check on that
result before calling `async_set_updated_data()`. The logic is unchanged.

---

## 3. Optimistic State on Writable Entities

### 3.1 Pattern

Every writable entity (switch, fan, time) gets:

```python
def __init__(self, ...):
    # ...existing code...
    self._pending_state: <type> | None = None
    self._cmd_lock = asyncio.Lock()
    self._pending_task: asyncio.Task | None = None

@callback
def _handle_coordinator_update(self) -> None:
    """Clear optimistic state when real broadcast data arrives."""
    self._cancel_pending_timeout()
    self._pending_state = None
    super()._handle_coordinator_update()

def _set_pending_state(self, value: <type>) -> None:
    self._pending_state = value
    self._arm_pending_timeout()
    self.async_write_ha_state()

def _arm_pending_timeout(self) -> None:
    self._cancel_pending_timeout()
    self._pending_task = self.hass.async_create_task(self._pending_timeout())

def _cancel_pending_timeout(self) -> None:
    if self._pending_task is not None:
        self._pending_task.cancel()
        self._pending_task = None

async def _pending_timeout(self) -> None:
    await asyncio.sleep(OPTIMISTIC_TIMEOUT_SECONDS)
    self._pending_state = None
    self._pending_task = None
    self.async_write_ha_state()

async def async_will_remove_from_hass(self) -> None:
    await super().async_will_remove_from_hass()
    self._cancel_pending_timeout()

@property
def is_on(self) -> bool | None:  # (or native_value, preset_mode, etc.)
    if self._pending_state is not None:
        return self._pending_state
    # ...existing coordinator data lookup...
```

Rule: every entity with optimistic state must clear pending state on any
confirmed coordinator update **or** when `OPTIMISTIC_TIMEOUT_SECONDS` expires.

### 3.2 Command guard behaviour

Two guard strategies depending on the command type:

#### Toggle commands (light only)

The light uses a single toggle command — sending it twice reverts the state.
Double-clicks must be blocked:

```python
async def async_turn_on(self, **kwargs) -> None:
    if self._cmd_lock.locked():
        return  # ignore — toggle already in flight
    async with self._cmd_lock:
        self._set_pending_state(True)
        success = await coordinator.async_send_command(cmd)
        if not success:
            self._pending_state = None
            self._cancel_pending_timeout()
            self.async_write_ha_state()
            raise HomeAssistantError(...)
    # No async_request_refresh() — reader loop handles it
```

If the user clicks while a toggle is in-flight → silently ignored.
The entity shows the optimistic state; the next broadcast confirms or
snaps back.

#### Target-state commands (heater, blower, ozone, jets, schedule)

These have distinct ON and OFF commands. Sending ON then OFF is safe —
both execute in sequence, final state = OFF. The "undo" scenario works:

```python
async def async_turn_on(self, **kwargs) -> None:
    if self.is_on:
        return  # already at target (optimistic or real)
    async with self._cmd_lock:
        self._set_pending_state(True)
        success = await coordinator.async_send_command(cmd)
        if not success:
            self._pending_state = None
            self._cancel_pending_timeout()
            self.async_write_ha_state()
            raise HomeAssistantError(...)
```

If the user clicks ON then immediately OFF:
1. ON acquires lock, sets optimistic=True, sends ON command, releases lock
2. OFF acquires lock, sets optimistic=False, sends OFF command, releases lock
3. Both commands reach the spa in order → final state = OFF ✓
4. UI shows: ON → OFF → broadcast confirms OFF

The `_cmd_lock` here serializes commands per-entity (not blocking — each
command completes in ~1 s including cooldown). Combined with the
coordinator-level cooldown, this prevents bus flooding.

#### Jets reliability policy (RS485 half-duplex)

Because the physical bus is half-duplex, jets commands get one bounded retry:

- Send jets target command once with optimistic UI.
- Wait for next broadcast confirmation.
- If broadcast does not match requested jets target, send **one** retry.
- If still mismatched, clear optimistic state and snap back.

This keeps bus traffic low while improving reliability under collision/noise.

### 3.3 Entity-by-entity changes

| Entity class | File | `_pending_state` type | Guard | Notes |
|---|---|---|---|---|
| `SpaLightSwitch` | `switch.py` | `bool \| None` | Toggle-lock (ignore) | Remove `asyncio.sleep(1.0)` |
| `SpaHeaterSwitch` | `switch.py` | `bool \| None` | Target-state | — |
| `SpaBlowerSwitch` | `switch.py` | `bool \| None` | Target-state | — |
| `SpaOzoneSwitch` | `switch.py` | `bool \| None` | Target-state | — |
| `SpaScheduleSlotSwitch` | `switch.py` | `bool \| None` | Target-state | — |
| `SpaPumpFan` | `fan.py` | `str \| None` (off/low/high) | Target-state | Keep max 1 bounded retry on mismatch |
| `SpaScheduleTime` | `time.py` | `tuple \| None` | Target-state | — |
| `SpaClimate` | `climate.py` | — | Already has debounce ✓ | Remove `async_request_refresh()` call; reader loop handles confirmation |
| `SpaSyncClockButton` | `button.py` | N/A (no state) | Lock (ignore while in-flight) | Remove `async_request_refresh()` |

### 3.4 Snap-back behaviour

If a command fails silently (RS485 bus collision, controller didn't process
it), the next broadcast arrives with the old state. Because
`_handle_coordinator_update` clears `_pending_state`, the entity reverts
to the real value. The user sees the switch "snap back" — clear visual
feedback that something went wrong, prompting a retry.

---

## 4. What Gets Deleted

| Code | Location | Why |
|---|---|---|
| `asyncio.sleep(1.0)` after light toggle | `switch.py` `_send_toggle()` | Broadcast stream confirms within 1–2 s |
| `asyncio.sleep(0.1)` after command send | `coordinator.py` `async_send_command()` | Optional; can remove or keep (harmless) |
| `await coordinator.async_request_refresh()` | All entity `async_turn_on/off`, `async_set_value`, `async_press` | Reader loop pushes updates automatically |
| `SCAN_INTERVAL = 30` | `const.py` | Change to 60 (fallback/health-check only) |
| Fan retry loop (3 attempts) | `fan.py` `_send_pump_command()` | Replace with max 1 bounded retry on mismatch |
| `_read_broadcast()` method | `coordinator.py` | Replaced by `_reader_loop()` |
| Per-command TCP connect/disconnect | `coordinator.py` `async_send_command()` | Uses persistent connection |

---

## 5. Implementation Order

1. **`coordinator.py`** — Persistent connection, reader loop, rewrite
   `async_send_command`, reconnect logic, move ozone/clock sync into
   reader path. This is the foundational change.
2. **`__init__.py`** — Call `coordinator.async_setup()`, handle shutdown.
3. **`const.py`** — Change `SCAN_INTERVAL` to 60 and add:
   - `AVAILABILITY_GRACE_SECONDS = 10`
   - `RX_STALE_SECONDS = 15`
   - `OPTIMISTIC_TIMEOUT_SECONDS = 10`
   Then replace hardcoded timing values with these constants.
4. **`switch.py`** — Add optimistic state + command guards to all 5 switch
   classes. Remove sleep and refresh calls.
5. **`fan.py`** — Add optimistic state, keep max 1 bounded retry for jets, remove refresh calls.
6. **`time.py`** — Add optimistic state, remove refresh call.
7. **`climate.py`** — Remove `async_request_refresh()` call from
   `_debounced_send`. Keep the debounce timer (it's correct).
8. **`button.py`** — Add in-flight lock, remove refresh call.
9. **Tests** — Update `test_entities_runtime.py`, `test_fan_entity_runtime.py`
   to mock the persistent connection model instead of polling. Add tests for
   optimistic state and snap-back.
10. **Shutdown/race tests** — Add coordinator tests for:
   - no reconnect task scheduled after unload (`_stopped=True`)
   - only one reconnect task while repeated failures happen
   - stale RX health-check forces reconnect
   - parser consumption prevents duplicate updates
   - reader uses local socket reference (no `None.read()` race during close)
11. **Optimistic timeout tests** — Add tests for:
   - pending state auto-clears after `OPTIMISTIC_TIMEOUT_SECONDS`
   - pending timeout is canceled when broadcast confirmation arrives
   - timeout snap-back writes updated HA state exactly once
   - pending timeout task is canceled on entity removal (`async_will_remove_from_hass`)
12. **Availability propagation tests** — Add tests that each platform entity
   reports availability from coordinator grace logic via shared base entity.
13. **HA lifecycle tests** — Add tests for:
   - `async_unload_entry` does not call shutdown when platform unload fails
   - `async_unload_entry` calls shutdown only after unload succeeds
   - `entry.runtime_data` is used for coordinator access

---

## 6. Testing Checklist

- [ ] Coordinator connects on setup, reader loop receives broadcast frames
- [ ] Coordinator reconnects with backoff on disconnect
- [ ] Coordinator reconnects on write failure
- [ ] `async_send_command` writes on persistent socket, returns True
- [ ] `async_send_command` returns False and triggers reconnect on failure
- [ ] Entity shows optimistic state immediately after command send
- [ ] Entity clears optimistic state on coordinator update (broadcast)
- [ ] Entity reverts (snap-back) when broadcast shows different state
- [ ] Light: double-click blocked by lock (second click ignored)
- [ ] Heater: rapid ON+OFF both execute, final state = OFF
- [ ] Fan: optimistic preset_mode, max 1 bounded retry on mismatch
- [ ] Climate: debounce still works, no refresh call
- [ ] Ozone/clock sync still trigger from broadcast data
- [ ] Clean shutdown closes connection and cancels reader task
- [ ] No reconnect occurs after config entry unload
- [ ] At most one reconnect task exists at any time
- [ ] Stale stream detection triggers reconnect even if socket stays open
- [ ] Parser consumes buffer so one frame does not generate repeated updates
- [ ] Availability remains True during `AVAILABILITY_GRACE_SECONDS` after disconnect
- [ ] Availability becomes False after grace window if still disconnected
- [ ] Pending optimistic state auto-clears after `OPTIMISTIC_TIMEOUT_SECONDS`
- [ ] Pending timeout is canceled when matching broadcast arrives
- [ ] Pending timeout task is canceled on entity removal
- [ ] All entities use coordinator grace availability consistently
- [ ] Coordinator close path awaits socket shutdown (`wait_closed`) safely
- [ ] Unload failure keeps coordinator running; unload success then calls shutdown
- [ ] Coordinator is read from `entry.runtime_data` in setup/unload flow

---

## 7. Reference: Key Files

| File | Role |
|---|---|
| `coordinator.py` | Main refactor target — connection management + data flow |
| `switch.py` | 5 switch classes: light, heater, blower, ozone, schedule |
| `fan.py` | Pump fan with preset modes |
| `climate.py` | Thermostat with debounce |
| `time.py` | Schedule time slots |
| `button.py` | Clock sync button |
| `__init__.py` | Entry setup, coordinator lifecycle |
| `const.py` | Constants (`SCAN_INTERVAL`, `COMMAND_COOLDOWN`, `AVAILABILITY_GRACE_SECONDS`, `RX_STALE_SECONDS`, `OPTIMISTIC_TIMEOUT_SECONDS`) |
| `protocol.py` | `find_frames()`, `unescape_frame()`, `is_broadcast()`, `validate_frame()` |
| `adapters/p25b85.py` | `parse_status()`, all `build_*_command()` methods |
| `adapters/base.py` | `ModelAdapter` protocol definition |

---

## 8. Final Decisions (ELI5)

### D1) Unload guarantees **zero** background tasks

Think of the integration like bedtime.

- If we say "go to bed," every little helper (reader task, reconnect timer) must stop.
- If one helper wakes up later and reconnects, it's like a kid sneaking out of bed.
- **Decision:** yes. On successful unload, we enforce strict shutdown (cancel reader + reconnect task, set `_stopped=True`).

### D2) Entities use grace mode on disconnect

Think of a walkie-talkie.

- If connection drops, we can show "not available" right away (strict mode).
- Or we can wait a tiny bit in case messages resume quickly (grace mode).
- **Decision:** grace mode with `AVAILABILITY_GRACE_SECONDS = 10`.

### D3) Optimistic state auto-expires

Think of putting a sticker on a toy saying "done" before checking it.

- Optimistic state is that sticker.
- If no real update arrives, sticker can stay wrong too long.
- **Decision:** add pending timeout with `OPTIMISTIC_TIMEOUT_SECONDS = 10`.

### D4) Disconnected send triggers reconnect

Think of mailing a letter with no mailbox.

- Current behavior: "can't send" and return False.
- Better behavior: also start reconnect attempt right away.
- **Decision:** return failure to entity *and* schedule reconnect immediately.

### D5) Startup wait is bounded

Think of opening a radio and waiting for first song.

- If we fail immediately, users see unavailable even if first frame is 1-2s away.
- If we wait forever, startup hangs.
- **Decision:** bounded wait of `TCP_TIMEOUT` for first frame, then fallback refresh determines status.
