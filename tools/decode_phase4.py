#!/usr/bin/env python3
"""
Phase 4 — Detailed command frame decoder.

Decodes the structure of the 22-byte command frames captured from the PB554 panel.
"""
from __future__ import annotations

# All captured command frames (22 bytes each, addr 0x01)
COMMANDS = {
    "PUMP ON (OFF→low)":  "1a0120103ca110a10202000000c00056007dd2146b1d",
    "PUMP HIGH (low→high)": "1a0120103ca110a10604000000c0005600fc1221c61d",
    "PUMP OFF (high→OFF)": "1a0120103ca110a10400000000c0005600735738e91d",
    "LIGHT ON":           "1a0120103ca110a10000404000c00056003031eeb21d",
    "LIGHT OFF":          "1a0120103ca110a10000404000c00056003031eeb21d",
    "TEMP UP":            "1a0120103ca110a10000808000c00057005aa3207f1d",
    "TEMP DOWN":          "1a0120103ca110a10000808000c0005600dd0ff87e1d",
}

def decode_frame(hex_str: str) -> list[int]:
    return list(bytes.fromhex(hex_str))


def main():
    print("Phase 4 — Command Frame Structure Analysis")
    print("=" * 70)
    print()

    # First, show all frames aligned byte-by-byte
    print("Byte-by-byte layout (0-indexed):")
    print(f"{'Byte':>4}  ", end="")
    for name in COMMANDS:
        print(f"{name[:10]:>10}", end=" ")
    print()
    print("-" * 90)

    frames = {name: decode_frame(h) for name, h in COMMANDS.items()}
    max_len = max(len(f) for f in frames.values())

    # Find static bytes (same across all frames)
    static_mask = []
    for i in range(max_len):
        vals = set()
        for f in frames.values():
            if i < len(f):
                vals.add(f[i])
        static_mask.append(len(vals) == 1)

    for i in range(max_len):
        marker = "  " if static_mask[i] else "◀ "
        print(f"{i:4d}: ", end="")
        for name in COMMANDS:
            f = frames[name]
            if i < len(f):
                print(f"      0x{f[i]:02x}", end=" ")
            else:
                print(f"         -", end=" ")
        print(f"  {marker}{'STATIC' if static_mask[i] else 'VARIES'}")

    # Identify structure
    print("\n\n" + "=" * 70)
    print("FRAME STRUCTURE ANALYSIS")
    print("=" * 70)

    ref = decode_frame(list(COMMANDS.values())[0])
    print(f"""
Frame: 22 bytes total
  Byte  0: 0x{ref[0]:02X}  = FRAME_START
  Byte  1: 0x{ref[1]:02X}  = Destination address (controller = 0x01)
  Byte  2: 0x{ref[2]:02X}  = ???
  Byte  3: 0x{ref[3]:02X}  = Frame length or type?
  Byte  4: 0x{ref[4]:02X}  = ???
  Byte  5: 0x{ref[5]:02X}  = ???
  Byte  6: 0x{ref[6]:02X}  = ???
  Byte  7: 0x{ref[7]:02X}  = ???
""")

    print("\nVARYING BYTES (command payload):")
    print("-" * 70)
    for i in range(max_len):
        if not static_mask[i]:
            print(f"\n  Byte {i}:")
            for name, f in frames.items():
                if i < len(f):
                    print(f"    {name:25s} = 0x{f[i]:02X} ({f[i]:3d}) {f[i]:08b}")

    # Specific analysis
    print("\n\n" + "=" * 70)
    print("COMMAND BYTE INTERPRETATION")
    print("=" * 70)

    print("\nBytes 8-9 appear to be PUMP command bytes:")
    for name, f in frames.items():
        print(f"  {name:25s}: byte8=0x{f[8]:02X} byte9=0x{f[9]:02X}")

    print("\nBytes 10-11 appear to be LIGHT/TEMP button flags:")
    for name, f in frames.items():
        print(f"  {name:25s}: byte10=0x{f[10]:02X} byte11=0x{f[11]:02X}")

    print("\nByte 15 appears to be SETPOINT value:")
    for name, f in frames.items():
        print(f"  {name:25s}: byte15=0x{f[15]:02X} ({f[15]}°F = {(f[15]-32)*5/9:.1f}°C)")

    print("\nBytes 17-20 appear to be CRC/checksum (last 4 before frame end):")
    for name, f in frames.items():
        crc = f[17:21]
        print(f"  {name:25s}: {' '.join(f'0x{b:02X}' for b in crc)}")

    # Key insight: LIGHT ON == LIGHT OFF (same frame = toggle!)
    print("\n\n" + "=" * 70)
    print("KEY INSIGHTS")
    print("=" * 70)
    print()
    print("1. LIGHT ON and LIGHT OFF are THE SAME FRAME → it's a TOGGLE command")
    print(f"   Frame: {COMMANDS['LIGHT ON']}")
    print()
    print("2. PUMP commands encode current+target state in bytes 8-9:")
    print(f"   OFF→low:  byte8=0x02, byte9=0x02 (pump=low, target=low)")
    print(f"   low→high: byte8=0x06, byte9=0x04 (pump=both?, target=high)")
    print(f"   high→OFF: byte8=0x04, byte9=0x00 (pump=high, target=off)")
    print()
    print("3. TEMP UP/DOWN use same button flag (0x80,0x80) but different setpoint:")
    print(f"   TEMP UP:   setpoint byte15=0x57 (87°F = 30.6°C)")
    print(f"   TEMP DOWN: setpoint byte15=0x56 (86°F = 30.0°C)")
    print()
    print("4. All frames are 22 bytes, addressed to 0x01, with last 4 bytes as CRC")
    print()
    print("5. Frame header (bytes 0-7) is constant: 1a 01 20 10 3c a1 10 a1")
    print()

    # Reconstruct command format
    print("=" * 70)
    print("PROPOSED COMMAND FORMAT (22 bytes)")
    print("=" * 70)
    print("""
  Offset  Size  Field
  ------  ----  -----
  0       1     Frame start (0x1A)
  1       1     Destination (0x01 = controller)
  2-7     6     Header/preamble (0x20 0x10 0x3C 0xA1 0x10 0xA1) — fixed
  8       1     Pump command byte 1
  9       1     Pump command byte 2
  10      1     Button flag high (0x40=light, 0x80=temp, 0x00=pump-only)
  11      1     Button flag low  (0x40=light, 0x80=temp, 0x00=pump-only)
  12-14   3     Always 0x00 0xC0 0x00
  15      1     Setpoint temperature (°F)
  16      1     Always 0x00
  17-20   4     CRC-32 or checksum
  21      1     Frame end (0x1D)
""")


if __name__ == "__main__":
    main()

