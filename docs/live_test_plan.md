# Live Test Plan — Joyonway P25B85 HA Integration

> Checklist for live testing at the spa. Mark each item ✅ / ❌ / ⚠️.

## Prerequisites

- [ ] HA running with `joyonway` integration loaded
- [ ] EW11 bridge connected (RS485 bridge entity = ON)
- [ ] Spa powered on, panel accessible

---

## 1. Connectivity

### 1.1 Initial connection
- **Given:** Integration configured with correct IP/port
- **When:** HA starts
- **Then:** RS485 bridge binary_sensor = **connected**
- Result: 

### 1.2 Bridge disconnect
- **Given:** EW11 powered off
- **When:** Wait 10s
- **Then:** RS485 bridge binary_sensor = **disconnected**
- Result: 

### 1.3 Bridge reconnect
- **Given:** EW11 powered back on
- **When:** Wait reconnect (~30s)
- **Then:** RS485 bridge = **connected**, sensors update
- Result: 

---

## 2. Sensors (read-only)

### 2.1 Water temperature
- **Given:** Spa idle, heater off
- **When:** Observe sensor
- **Then:** Water temperature sensor shows current °C (integer)
- Result: 

### 2.2 Heater state — off
- **Given:** Heater off
- **When:** Observe sensor
- **Then:** Heater state sensor = **off**
- Result: 

### 2.3 Heater state — heating
- **Given:** Heater heating
- **When:** Raise setpoint above water temp on panel
- **Then:** Heater state sensor = **heating**
- Result: 

### 2.4 Heater state — circulation
- **Given:** Heater circulation only
- **When:** Setpoint ≈ water temp
- **Then:** Heater state sensor = **circulation**
- Result: 

### 2.5 Pump state — off
- **Given:** Pump off
- **When:** Observe sensor
- **Then:** Pump state sensor = **off**
- Result: 

### 2.6 Pump state — low
- **Given:** Pump low (filtration)
- **When:** Press pump on panel once
- **Then:** Pump state sensor = **low**
- Result: 

### 2.7 Pump state — high
- **Given:** Pump high (jets)
- **When:** Press pump on panel again
- **Then:** Pump state sensor = **high**
- Result: 

### 2.8 Heater state — disinfection
- **Given:** Disinfection running
- **When:** Wait for scheduled cycle or trigger
- **Then:** Heater state sensor = **disinfection**
- Result: 

---

## 3. Climate (Thermostat)

### 3.1 Display
- **Given:** Thermostat card visible
- **When:** Observe
- **Then:** Shows current water temp + current setpoint
- Result: 

### 3.2 Setpoint increase
- **Given:** Setpoint = 36°C
- **When:** Drag slider to 38°C in HA
- **Then:** Setpoint updates to 38°C; panel confirms new setpoint
- Result: 

### 3.3 Setpoint decrease
- **Given:** Setpoint = 38°C
- **When:** Drag slider to 35°C in HA
- **Then:** Setpoint updates to 35°C; panel confirms
- Result: 

### 3.4 Debounce
- **Given:** Slider dragged rapidly
- **When:** Drag slider back and forth
- **Then:** Only final value sent after 1.5s debounce (no command flood)
- Result: 

### 3.5 Panel setpoint change
- **Given:** Setpoint changed on panel
- **When:** Change setpoint on PB554
- **Then:** HA thermostat updates within next poll cycle
- Result: 

---

## 4. Light Switch

### 4.1 Turn on
- **Given:** Light is OFF
- **When:** Toggle light switch ON in HA
- **Then:** Light turns on; switch shows ON
- Result: 

### 4.2 Turn off
- **Given:** Light is ON
- **When:** Toggle light switch OFF in HA
- **Then:** Light turns off; switch shows OFF
- Result: 

### 4.3 Panel toggle
- **Given:** Light toggled on panel
- **When:** Press light on PB554
- **Then:** HA switch state updates accordingly
- Result: 

### 4.4 Unknown state safety guard
- **Given:** State unknown (first boot)
- **When:** Toggle light in HA
- **Then:** Switch refuses action (safety guard)
- Result: 

---

## 5. Heater Switch

### 5.1 Turn on
- **Given:** Heater is OFF (setpoint < water temp)
- **When:** Turn heater switch ON in HA
- **Then:** Heater starts; heater state = heating
- Result: 

### 5.2 Turn off
- **Given:** Heater is ON
- **When:** Turn heater switch OFF in HA
- **Then:** Heater stops; heater state = off or circulation
- Result: 

### 5.3 Panel toggle
- **Given:** Heater toggled on panel
- **When:** Change setpoint on PB554
- **Then:** HA heater switch state updates
- Result: 

---

## 6. Blower Switch

### 6.1 Turn on
- **Given:** Blower is OFF
- **When:** Turn blower switch ON in HA
- **Then:** Blower activates; byte[28] bit 3 set
- Result: 

### 6.2 Turn off
- **Given:** Blower is ON
- **When:** Turn blower switch OFF in HA
- **Then:** Blower stops; byte[28] bit 3 cleared
- Result: 

### 6.3 Panel toggle
- **Given:** Blower toggled on panel
- **When:** Press blower on PB554
- **Then:** HA blower switch state updates
- Result: 

---

## 7. Jets (Fan Entity)

### 7.1 Off → low
- **Given:** Jets OFF
- **When:** Set fan to preset "low" in HA
- **Then:** Pump goes to low speed (filtration)
- Result: 

### 7.2 Off → high
- **Given:** Jets OFF
- **When:** Set fan to preset "high" in HA
- **Then:** Pump goes to high speed (massage jets)
- Result: 

### 7.3 Low → high
- **Given:** Jets LOW
- **When:** Set fan to preset "high" in HA
- **Then:** Pump transitions low→high
- Result: 

### 7.4 High → off
- **Given:** Jets HIGH
- **When:** Turn fan OFF in HA
- **Then:** Pump stops (may cycle through states)
- Result: 

### 7.5 Low → off
- **Given:** Jets LOW
- **When:** Turn fan OFF in HA
- **Then:** Pump stops
- Result: 

### 7.6 Panel cycle
- **Given:** Pump cycled on panel
- **When:** Press pump on PB554
- **Then:** Fan entity preset updates in HA
- Result: 

---

## 8. Heat Schedule (Time + Switch)

### 8.1 Read slot 1 times
- **Given:** Heat slot 1 enabled
- **When:** Observe time entities
- **Then:** Heat slot 1 start/end show correct HH:MM
- Result: 

### 8.2 Write slot 1 start
- **Given:** Heat slot 1 start = 12:00
- **When:** Change to 14:00 in HA
- **Then:** Panel/broadcast updates to 14:00
- Result: 

### 8.3 Write slot 1 end
- **Given:** Heat slot 1 end = 16:00
- **When:** Change to 18:00 in HA
- **Then:** Panel/broadcast updates to 18:00
- Result: 

### 8.4 Disable slot 1
- **Given:** Heat slot 1 enabled
- **When:** Toggle heat slot 1 switch OFF
- **Then:** Slot disabled (broadcast shows 00:00 or flag cleared)
- Result: 

### 8.5 Re-enable slot 1
- **Given:** Heat slot 1 disabled
- **When:** Toggle heat slot 1 switch ON
- **Then:** Slot re-enabled with previous times restored
- Result: 

### 8.6 Slot 2
- **Given:** Heat slot 2
- **When:** Repeat 8.1–8.5 for slot 2
- **Then:** Same behavior
- Result: 

### 8.7 Panel schedule change
- **Given:** Schedule changed on panel
- **When:** Modify heat schedule on PB554
- **Then:** HA time entities update
- Result: 

---

## 9. Filter Schedule (Time + Switch)

### 9.1 Read slot 1 times
- **Given:** Filter slot 1 enabled
- **When:** Observe time entities
- **Then:** Filter slot 1 start/end show correct HH:MM
- Result: 

### 9.2 Write slot 1 start
- **Given:** Filter slot 1 start = 04:00
- **When:** Change to 06:00 in HA
- **Then:** Panel/broadcast updates to 06:00
- Result: 

### 9.3 Write slot 1 end
- **Given:** Filter slot 1 end = 08:00
- **When:** Change to 10:00 in HA
- **Then:** Panel/broadcast updates to 10:00
- Result: 

### 9.4 Disable slot 1
- **Given:** Filter slot 1 enabled
- **When:** Toggle filter slot 1 switch OFF
- **Then:** Slot disabled
- Result: 

### 9.5 Re-enable slot 1
- **Given:** Filter slot 1 disabled
- **When:** Toggle filter slot 1 switch ON
- **Then:** Slot re-enabled with previous times
- Result: 

### 9.6 Slot 2
- **Given:** Filter slot 2
- **When:** Repeat 9.1–9.5 for slot 2
- **Then:** Same behavior
- Result: 

### 9.7 Panel schedule change
- **Given:** Schedule changed on panel
- **When:** Modify filter schedule on PB554
- **Then:** HA time entities update
- Result: 

---

## 10. Clock Sync (Button)

### 10.1 Sync wrong clock
- **Given:** Spa clock shows wrong time
- **When:** Press "Sync clock" button in HA
- **Then:** Spa clock updates to current HA time
- Result: 

### 10.2 Sync correct clock
- **Given:** Spa clock correct
- **When:** Press button again
- **Then:** Spa clock stays correct (no drift)
- Result: 

### 10.3 Verify diagnostic sensor
- **Given:** After sync
- **When:** Observe spa_datetime diagnostic sensor
- **Then:** Shows time matching HA (within seconds)
- Result: 

---

## 11. Diagnostic Entities (disabled by default)

### 11.1 Spa clock sensor
- **Given:** Spa clock sensor enabled
- **When:** Observe
- **Then:** Shows spa's internal datetime
- Result: 

### 11.2 Raw pump byte sensor
- **Given:** Raw pump byte sensor enabled
- **When:** Observe
- **Then:** Shows hex value matching pump state
- Result: 

### 11.3 Raw heater byte sensor
- **Given:** Raw heater byte sensor enabled
- **When:** Observe
- **Then:** Shows hex value matching heater state
- Result: 

---

## 12. Edge Cases & Robustness

### 12.1 Command cooldown
- **Given:** HA sends command
- **When:** Immediately send another
- **Then:** Second command waits (1.0s cooldown)
- Result: 

### 12.2 WiFi loss recovery
- **Given:** EW11 loses WiFi mid-operation
- **When:** Wait for reconnect
- **Then:** Integration recovers, entities go unavailable then back
- Result: 

### 12.3 Command queue ordering
- **Given:** Multiple commands queued
- **When:** Send schedule + light + temp changes
- **Then:** All execute in order without corruption
- Result: 

### 12.4 Pump auto-off timeout
- **Given:** Jets running 20 min
- **When:** Pump auto-off triggers (20 min timeout)
- **Then:** HA fan entity updates to OFF when pump stops
- Result: 

---

## Notes / Observations

_Write any unexpected behaviors, new byte values, or panel differences here:_

```
(free space for notes)
```
