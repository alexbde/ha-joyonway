#!/usr/bin/env python3
"""
Test Modbus CRC-16 hypothesis with 2-byte CRC at various positions.

The hint claims last 2 bytes are CRC-16/MODBUS. Our frames have 0x1A/0x1D framing
so the CRC would be the last 2 bytes BEFORE 0x1D, or perhaps the 2 bytes before
the end delimiter.

Test various splits:
- CRC = bytes[-3:-1] (last 2 before 0x1D)
- CRC = bytes[19:21] with data = bytes[1:19]
- CRC = bytes[17:19] with data = bytes[1:17]
- etc.

Also test the Modbus property: CRC over entire message (including CRC) = 0x0000
"""
from __future__ import annotations
import struct

COMMANDS = [
    ("PUMP_ON",    bytes.fromhex("1a0120103ca110a10202000000c00056007dd2146b1d")),
    ("PUMP_HIGH",  bytes.fromhex("1a0120103ca110a10604000000c0005600fc1221c61d")),
    ("PUMP_OFF",   bytes.fromhex("1a0120103ca110a10400000000c0005600735738e91d")),
    ("LIGHT",      bytes.fromhex("1a0120103ca110a10000404000c00056003031eeb21d")),
    ("TEMP_UP",    bytes.fromhex("1a0120103ca110a10000808000c00057005aa3207f1d")),
    ("TEMP_DOWN",  bytes.fromhex("1a0120103ca110a10000808000c0005600dd0ff87e1d")),
]


def modbus_crc16(data: bytes) -> int:
    """Standard Modbus RTU CRC-16 (poly 0xA001, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def verify_modbus(packet: bytes) -> bool:
    """Run CRC over entire packet; result should be 0 if CRC is valid."""
    return modbus_crc16(packet) == 0x0000


def main():
    print("Modbus CRC-16 Hypothesis Test")
    print("=" * 70)

    for name, frame in COMMANDS:
        print(f"\n{'─' * 70}")
        print(f"  {name}: {frame.hex()}")
        print(f"  Length: {len(frame)} bytes")
        print()

        # Test 1: CRC = last 2 bytes before 0x1D (bytes[-3:-1])
        # In Modbus RTU, CRC is appended low byte first
        crc_field = frame[-3:-1]  # bytes[19:21]
        crc_val_le = crc_field[0] | (crc_field[1] << 8)  # low byte first
        crc_val_be = (crc_field[0] << 8) | crc_field[1]  # big endian

        # Data = everything between start and CRC (bytes[1:-3])
        data1 = frame[1:-3]
        computed1 = modbus_crc16(data1)
        print(f"  Split A: data=bytes[1:-3] ({len(data1)}B), CRC=bytes[-3:-1]={crc_field.hex()}")
        print(f"    Computed: 0x{computed1:04X}, Expected LE: 0x{crc_val_le:04X}, BE: 0x{crc_val_be:04X}")
        print(f"    Match: {'✅ LE!' if computed1 == crc_val_le else '✅ BE!' if computed1 == crc_val_be else '❌'}")

        # Test 1b: Verify entire inner message (bytes[1:-1], skip 0x1A and 0x1D)
        inner = frame[1:-1]
        verify1b = modbus_crc16(inner)
        print(f"  Verify: modbus_crc16(bytes[1:-1]) = 0x{verify1b:04X} {'✅ = 0!' if verify1b == 0 else '❌'}")

        # Test 2: CRC = bytes[19:21], data = bytes[0:19] (include start byte)
        if len(frame) >= 21:
            crc_field2 = frame[19:21]
            crc_le2 = crc_field2[0] | (crc_field2[1] << 8)
            data2 = frame[0:19]
            computed2 = modbus_crc16(data2)
            print(f"  Split B: data=bytes[0:19], CRC=bytes[19:21]={crc_field2.hex()}")
            print(f"    Computed: 0x{computed2:04X}, Expected LE: 0x{crc_le2:04X}")
            print(f"    Match: {'✅' if computed2 == crc_le2 else '❌'}")

        # Test 3: CRC = bytes[17:19], data = bytes[1:17]
        crc_field3 = frame[17:19]
        crc_le3 = crc_field3[0] | (crc_field3[1] << 8)
        crc_be3 = (crc_field3[0] << 8) | crc_field3[1]
        data3 = frame[1:17]
        computed3 = modbus_crc16(data3)
        print(f"  Split C: data=bytes[1:17] ({len(data3)}B), CRC=bytes[17:19]={crc_field3.hex()}")
        print(f"    Computed: 0x{computed3:04X}, Expected LE: 0x{crc_le3:04X}, BE: 0x{crc_be3:04X}")
        print(f"    Match: {'✅ LE!' if computed3 == crc_le3 else '✅ BE!' if computed3 == crc_be3 else '❌'}")

        # Test 4: CRC = bytes[19:21], data = bytes[1:19]
        if len(frame) >= 21:
            crc_field4 = frame[19:21]
            crc_le4 = crc_field4[0] | (crc_field4[1] << 8)
            crc_be4 = (crc_field4[0] << 8) | crc_field4[1]
            data4 = frame[1:19]
            computed4 = modbus_crc16(data4)
            print(f"  Split D: data=bytes[1:19] ({len(data4)}B), CRC=bytes[19:21]={crc_field4.hex()}")
            print(f"    Computed: 0x{computed4:04X}, Expected LE: 0x{crc_le4:04X}, BE: 0x{crc_be4:04X}")
            print(f"    Match: {'✅ LE!' if computed4 == crc_le4 else '✅ BE!' if computed4 == crc_be4 else '❌'}")

        # Test 5: Maybe CRC covers bytes[0:-3] (including 0x1A start)
        data5 = frame[0:-3]
        crc_field5 = frame[-3:-1]
        crc_le5 = crc_field5[0] | (crc_field5[1] << 8)
        computed5 = modbus_crc16(data5)
        print(f"  Split E: data=bytes[0:-3] (incl 0x1A), CRC=bytes[-3:-1]")
        print(f"    Computed: 0x{computed5:04X}, Expected LE: 0x{crc_le5:04X}")
        print(f"    Match: {'✅' if computed5 == crc_le5 else '❌'}")

        # Test 6: What if CRC covers only data payload (bytes[4:17], after header)
        data6 = frame[4:17]
        for crc_start in [17, 19]:
            crc_f = frame[crc_start:crc_start+2]
            crc_le = crc_f[0] | (crc_f[1] << 8)
            computed6 = modbus_crc16(data6)
            match = '✅' if computed6 == crc_le else '❌'
            print(f"  Split F: data=bytes[4:17], CRC=bytes[{crc_start}:{crc_start+2}]={crc_f.hex()} → 0x{computed6:04X} vs 0x{crc_le:04X} {match}")

    # Now test on bus frames
    print(f"\n\n{'=' * 70}")
    print("BUS FRAME TEST (non-broadcast frames from capture)")
    print("=" * 70)

    import os
    captures_dir = os.path.join(os.path.dirname(__file__), "..", "captures_phase4")
    baseline_file = os.path.join(captures_dir, "00_cmd_pump_on_baseline.bin")

    if os.path.exists(baseline_file):
        with open(baseline_file, "rb") as f:
            raw = f.read()

        # Extract frames
        frames = []
        i = 0
        while i < len(raw):
            if raw[i] == 0x1A:
                j = i + 1
                while j < len(raw):
                    if raw[j] == 0x1D:
                        frames.append(raw[i:j+1])
                        i = j + 1
                        break
                    j += 1
                else:
                    break
            else:
                i += 1

        # Test each frame: run modbus CRC over bytes[1:-1] (between delimiters)
        # If result = 0, the frame has valid Modbus CRC embedded
        by_length: dict[int, list] = {}
        for f in frames:
            by_length.setdefault(len(f), []).append(f)

        for length in sorted(by_length.keys()):
            group = by_length[length]
            # Test: modbus_crc16(bytes[1:-1]) == 0
            verify_hits = 0
            # Test: CRC = last 2 of inner, data = rest of inner
            split_le_hits = 0
            split_be_hits = 0
            total = min(len(group), 50)

            for f in group[:total]:
                inner = f[1:-1]  # between 0x1A and 0x1D
                if modbus_crc16(inner) == 0:
                    verify_hits += 1

                if len(inner) >= 3:
                    data = inner[:-2]
                    crc_le = inner[-2] | (inner[-1] << 8)
                    crc_be = (inner[-2] << 8) | inner[-1]
                    computed = modbus_crc16(data)
                    if computed == crc_le:
                        split_le_hits += 1
                    if computed == crc_be:
                        split_be_hits += 1

            print(f"\n  Length {length} ({len(group)} frames, tested {total}):")
            print(f"    verify(inner)==0:     {verify_hits}/{total}")
            print(f"    CRC=inner[-2:] (LE): {split_le_hits}/{total}")
            print(f"    CRC=inner[-2:] (BE): {split_be_hits}/{total}")
            if verify_hits > 0 or split_le_hits > 0 or split_be_hits > 0:
                print(f"    🎯 POTENTIAL MATCH!")
                # Show details of matching frame
                for f in group[:total]:
                    inner = f[1:-1]
                    if modbus_crc16(inner) == 0:
                        print(f"    Verified: {f.hex()}")
                        break


if __name__ == "__main__":
    main()

