"""Quick script to visualize predicted HA device page 2-column layout."""
import sys
sys.path.insert(0, ".")

from custom_components.joyonway_p25b85.adapters.p25b85 import P25B85Adapter

adapter = P25B85Adapter()
descs = adapter.entity_descriptions()

# DE translations
names = {
    "water_temperature": "Wassertemperatur",
    "setpoint": "Solltemperatur",
    "status": "Status",
    "jets": "Duesen",
    "spa_datetime": "Datum & Uhrzeit",
}

# Switches (from switch.py registration order)
switch_items = [
    "Heizung",
    "Filtration",
    "Licht",
    "Geblaese",
    "Heiz-Slot 1",
    "Heiz-Slot 2",
    "Filter-Slot 1",
    "Filter-Slot 2",
]

# Buttons (from button.py registration order)
button_items = [
    "Uhr synchronisieren",
]

# Time (from time.py registration order)
time_items = [
    "Heiz-Slot 1 Start",
    "Heiz-Slot 1 Ende",
    "Heiz-Slot 2 Start",
    "Heiz-Slot 2 Ende",
    "Filter-Slot 1 Start",
    "Filter-Slot 1 Ende",
    "Filter-Slot 2 Start",
    "Filter-Slot 2 Ende",
]


def print_section(title, items):
    print(f"\n  {title} ({len(items)} entities)")
    print(f"  {'─' * 56}")
    for i in range(0, len(items), 2):
        left = items[i]
        right = items[i + 1] if i + 1 < len(items) else "(empty)"
        print(f"  │ {left:<25} │ {right:<25} │")
    print(f"  {'─' * 56}")


print("=" * 60)
print("  HA Device Page — Predicted 2-Column Layout (DE)")
print("=" * 60)

# Sensors
sensor_items = [
    names.get(d.key, d.name)
    for d in descs
    if d.platform == "sensor" and d.enabled_by_default
]
print_section("SENSORS", sensor_items)

# Controls: switches
print_section("SWITCHES", switch_items)

# Buttons
print_section("BUTTONS", button_items)

# Time
print_section("TIME ENTITIES", time_items)

print("\n  SEPARATE CARDS (rendered individually)")
print(f"  {'─' * 56}")
print("  │ Thermostat (climate)       │ Duesen (fan presets)     │")
print(f"  {'─' * 56}")

print("\n  DISABLED BY DEFAULT (not visible)")
print(f"  {'─' * 56}")
print("  │ RS485-Bridge               │ Datum & Uhrzeit          │")
print(f"  {'─' * 56}")
print()


