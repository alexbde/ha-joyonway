#!/usr/bin/env python3
"""Read and display schedule/datetime data from spa broadcast frames.

Shows:
- Spa clock (bytes 53-58)
- Schedule config byte (byte 19)
- Filter schedule config byte (byte 29)
- Decoded heat/filter schedule interpretation

Also decodes the captured command frames for reference.
"""

import asyncio
import os
import sys
from pathlib import Path

# Add protocol module directly (avoid HA dependency in __init__)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "custom_components" / "joyonway_p25b85"))

from protocol import (
    find_frames, unescape_frame, is_broadcast, pseudo_unescape,
    FRAME_START, FRAME_END, compute_crc, build_frame,
)

# Load .env
env_path = Path(__file__).resolve().parent.parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

HOST = os.environ.get("SPA_BRIDGE_HOST")
PORT_RAW = os.environ.get("SPA_BRIDGE_PORT", "8899")
if not HOST:
    raise SystemExit("Missing SPA_BRIDGE_HOST (set it in environment or .env)")
PORT = int(PORT_RAW)


def decode_broadcast(frame: bytes) -> dict | None:
    """Decode schedule and datetime info from a broadcast frame."""
    if len(frame) < 59:
        return None
    if frame[1] != 0xFF:
        return None

    result = {}

    # DateTime bytes 53-58
    dt_bytes = frame[53:59]
    result["clock"] = {
        "year": 2000 + dt_bytes[0],
        "month": dt_bytes[1],
        "day": dt_bytes[2],
        "hour": dt_bytes[3],
        "minute": dt_bytes[4],
        "second": dt_bytes[5],
        "raw": dt_bytes.hex(),
    }

    # Schedule byte 19
    result["schedule_config_byte19"] = f"0x{frame[19]:02X}"

    # Filter schedule byte 29
    result["filter_config_byte29"] = f"0x{frame[29]:02X}"

    # Also grab some surrounding bytes for analysis
    result["bytes_18_22"] = frame[18:23].hex()
    result["bytes_28_32"] = frame[28:33].hex()

    # Water temp & setpoint for context
    result["water_temp_f"] = frame[9]
    result["setpoint_f"] = frame[16]
    result["heater_byte14"] = f"0x{frame[14]:02X}"

    return result


def decode_command_frames():
    """Decode the known captured schedule/datetime command frames."""
    print("=" * 60)
    print("CAPTURED COMMAND FRAME ANALYSIS")
    print("=" * 60)

    frames = {
        "DateTime set (session 2)": "1a0120103ca210a1501b110515163500000087ecf6541d",
        "DateTime set (session 1)": "1a0120103ca210a1501b1105150f090000004cbc3d971d",
        "Filter schedule (session 2)": "1a0120103ca410a1aa0c000c00110012007b62bdb61d",
        "Filter schedule (session 1)": "1a0120103ca410a1aa0d000c0011001200f605b0ff1d",
        "Heat schedule (session 2)": "1a0120103ca310a1620c001000140016005787b0ed1d",
        "Heat schedule (session 1)": "1a0120103ca310a1620e001000140016004d48aa7f1d",
    }

    for name, wire_hex in frames.items():
        wire = bytes.fromhex(wire_hex)
        inner = pseudo_unescape(wire[1:-1])  # strip 0x1A/0x1D, then unescape
        payload = inner[:16]
        crc_bytes = inner[16:20]

        print(f"\n{'─' * 60}")
        print(f"  {name}")
        print(f"  Wire: {wire_hex}")
        print(f"  Payload (16 bytes): {payload.hex()}")
        print(f"  CRC (4 bytes LE):   {crc_bytes.hex()}")

        # Verify CRC
        computed = compute_crc(payload)
        import struct
        stored = struct.unpack('<I', crc_bytes)[0]
        crc_ok = computed == stored
        print(f"  CRC verify: {'✅' if crc_ok else '❌'} (computed=0x{computed:08X}, stored=0x{stored:08X})")

        cmd_type = payload[4]
        print(f"  Command type: 0x{cmd_type:02X}", end="")
        if cmd_type == 0xA2:
            print(" (DateTime set)")
            # Bytes 7-15: 50 1A 05 15 16 35 00 00 00
            # (note: 1B11 on wire decodes to 0x1A)
            print(f"    Byte 7:  0x{payload[7]:02X} (fixed prefix)")
            print(f"    Byte 8:  0x{payload[8]:02X} = {payload[8]} (year? day?)")
            print(f"    Byte 9:  0x{payload[9]:02X} = {payload[9]} (day? month?)")
            print(f"    Byte 10: 0x{payload[10]:02X} = {payload[10]} (month? hour?)")
            print(f"    Byte 11: 0x{payload[11]:02X} = {payload[11]} (hour? minute?)")
            print(f"    Byte 12: 0x{payload[12]:02X} = {payload[12]} (minute? second?)")
            print(f"    Byte 13: 0x{payload[13]:02X} = {payload[13]}")
            print(f"    Byte 14: 0x{payload[14]:02X} = {payload[14]}")
            print(f"    Byte 15: 0x{payload[15]:02X} = {payload[15]}")
            # Try interpretation: byte8=year(26?), byte9=day, byte10=month, byte11=hour, byte12=min, byte13=sec
            # Session 2: 1A 05 15 16 35 00 → year=26, day=5, month=21?? no...
            # Or: year low byte, day, month, hour, min, sec
            # 1A=26, 05=5, 15=21?? doesn't make sense for month
            # Try hex as decimal? 0x1A=26, 0x05=5, 0x15=21... 21st month impossible
            # Maybe it's: year=0x1A=26 (2026), month=5, day=0x15=21, hour=0x16=22, min=0x35=53?
            # Session 1: 1A 05 15 0F 09 00  → year=26, month=5, day=21, hour=15, minute=9, second=0?
            # That makes sense! Both were captured in May (month 5)
            print(f"\n    INTERPRETATION:")
            year = payload[8]
            month = payload[9]
            day = payload[10]
            hour = payload[11]
            minute = payload[12]
            second = payload[13]
            print(f"      Year: 20{year:02d}, Month: {month}, Day: {day}")
            print(f"      Time: {hour:02d}:{minute:02d}:{second:02d}")

        elif cmd_type == 0xA3:
            print(" (Heat schedule)")
            print(f"    Byte 7:  0x{payload[7]:02X} = {payload[7]} (config/flags)")
            print(f"    Byte 8:  0x{payload[8]:02X} = {payload[8]} (duration/slot)")
            print(f"    Byte 9:  0x{payload[9]:02X} = {payload[9]}")
            print(f"    Byte 10: 0x{payload[10]:02X} = {payload[10]} (slot 1 start hour?)")
            print(f"    Byte 11: 0x{payload[11]:02X} = {payload[11]}")
            print(f"    Byte 12: 0x{payload[12]:02X} = {payload[12]} (slot 1 end hour?)")
            print(f"    Byte 13: 0x{payload[13]:02X} = {payload[13]}")
            print(f"    Byte 14: 0x{payload[14]:02X} = {payload[14]} (slot 2 start hour?)")
            print(f"    Byte 15: 0x{payload[15]:02X} = {payload[15]}")
            print(f"\n    INTERPRETATION:")
            print(f"      Flags: 0x{payload[7]:02X} ({payload[7]:08b})")
            print(f"      Duration/count: {payload[8]} hours")
            print(f"      Slot 1: {payload[10]:02d}:00 – {payload[12]:02d}:00")
            print(f"      Slot 2 start: {payload[14]:02d}:00")

        elif cmd_type == 0xA4:
            print(" (Filter schedule)")
            print(f"    Byte 7:  0x{payload[7]:02X} = {payload[7]} (config/flags)")
            print(f"    Byte 8:  0x{payload[8]:02X} = {payload[8]} (duration/slot)")
            print(f"    Byte 9:  0x{payload[9]:02X} = {payload[9]}")
            print(f"    Byte 10: 0x{payload[10]:02X} = {payload[10]} (slot 1 start hour?)")
            print(f"    Byte 11: 0x{payload[11]:02X} = {payload[11]}")
            print(f"    Byte 12: 0x{payload[12]:02X} = {payload[12]} (slot 1 end hour?)")
            print(f"    Byte 13: 0x{payload[13]:02X} = {payload[13]}")
            print(f"    Byte 14: 0x{payload[14]:02X} = {payload[14]} (slot 2 start hour?)")
            print(f"    Byte 15: 0x{payload[15]:02X} = {payload[15]}")
            print(f"\n    INTERPRETATION:")
            print(f"      Flags: 0x{payload[7]:02X} ({payload[7]:08b})")
            print(f"      Duration/count: {payload[8]} hours")
            print(f"      Slot 1: {payload[10]:02d}:00 – {payload[12]:02d}:00")
            print(f"      Slot 2 start: {payload[14]:02d}:00")
        else:
            print()


async def read_broadcast():
    """Connect to the EW11 and display schedule/datetime data from broadcast."""
    print("\n" + "=" * 60)
    print("LIVE BROADCAST READING")
    print("=" * 60)
    print(f"Connecting to {HOST}:{PORT}...")

    reader, writer = await asyncio.open_connection(HOST, PORT)
    print("Connected. Reading broadcast frames...\n")

    buffer = b""
    count = 0
    max_frames = 10

    try:
        while count < max_frames:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=5.0)
            if not chunk:
                break
            buffer += chunk

            frames = find_frames(buffer)
            for raw_frame in frames:
                unescaped = unescape_frame(raw_frame, full=True)
                if not is_broadcast(unescaped):
                    continue

                data = decode_broadcast(unescaped)
                if data is None:
                    continue

                count += 1
                if count == 1:
                    print(f"Frame length: {len(unescaped)} bytes (unescaped)")
                    print(f"Water temp: {data['water_temp_f']}°F, Setpoint: {data['setpoint_f']}°F")
                    print(f"Heater state: {data['heater_byte14']}")
                    print()
                    print(f"{'─' * 40}")
                    print(f"SCHEDULE & CLOCK DATA:")
                    print(f"{'─' * 40}")

                clock = data["clock"]
                print(f"  [{count:02d}] Clock: {clock['year']}-{clock['month']:02d}-{clock['day']:02d} "
                      f"{clock['hour']:02d}:{clock['minute']:02d}:{clock['second']:02d}"
                      f"  (raw: {clock['raw']})")
                print(f"       Schedule config [19]: {data['schedule_config_byte19']}")
                print(f"       Filter config  [29]: {data['filter_config_byte29']}")
                print(f"       Bytes 18-22: {data['bytes_18_22']}")
                print(f"       Bytes 28-32: {data['bytes_28_32']}")

                if count >= max_frames:
                    break

            # Remove consumed data from buffer
            if frames:
                last_end = buffer.rfind(bytes([0x1D])) + 1
                buffer = buffer[last_end:]

    except asyncio.TimeoutError:
        print("(timeout waiting for data)")
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    # First show the command frame analysis
    decode_command_frames()

    # Then read live broadcast data
    await read_broadcast()


if __name__ == "__main__":
    asyncio.run(main())


