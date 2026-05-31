#!/usr/bin/env python3
"""Focused test: manually verify GCD on specific frame pairs."""
import json, os

ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}
def unescape(data):
    result = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x1B and i + 1 < len(data) and data[i + 1] in ESCAPE_MAP:
            result.append(ESCAPE_MAP[data[i + 1]])
            i += 2
        else:
            result.append(data[i])
            i += 1
    return bytes(result)
def parse_frame(hex_str):
    raw = bytes.fromhex(hex_str)
    inner = unescape(raw[1:-1])
    return inner

def gf2_degree(a): return a.bit_length() - 1 if a else -1
def gf2_mod(a, b):
    db = gf2_degree(b)
    if db < 0: raise ZeroDivisionError
    while True:
        da = gf2_degree(a)
        if da < db: return a
        a ^= b << (da - db)
def gf2_gcd(a, b):
    while b: a, b = b, gf2_mod(a, b)
    return a
def gf2_divmod(a, b):
    db = gf2_degree(b)
    if db < 0: raise ZeroDivisionError
    q = 0
    while True:
        da = gf2_degree(a)
        if da < db: return q, a
        shift = da - db
        q ^= 1 << shift
        a ^= b << shift

def make_crc_table(poly, reflected):
    table = []
    for i in range(256):
        crc = i if reflected else i << 24
        for _ in range(8):
            if reflected: crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            else: crc = ((crc << 1) & 0xFFFFFFFF) ^ poly if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table
def compute_crc(data, table, init, xor_out, reflected):
    crc = init
    for byte in data:
        if reflected: crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
        else: crc = ((crc << 8) & 0xFFFFFFFF) ^ table[((crc >> 24) ^ byte) & 0xFF]
    return crc ^ xor_out
def reflect_bits(v, w):
    r = 0
    for i in range(w):
        if v & (1 << i): r |= 1 << (w - 1 - i)
    return r


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    frames_raw = data["frames"]
    frames = {}
    for name, hex_str in frames_raw.items():
        inner = parse_frame(hex_str)
        frames[name] = inner

    print("=" * 70)
    print("  FOCUSED CRC EXTRACTION")
    print("=" * 70)

    # ─── 1. Build combined polynomials M*x^32 + CRC ───
    print("\n--- 1. Combined polys for each frame ---")

    # We need to determine: what bytes form M, what bytes form CRC, what endianness
    # Try: M = inner[0:16] (big-endian poly), CRC = inner[16:20]
    # Two CRC endianness options

    for crc_endian in ["le", "be"]:
        print(f"\n  CRC endian: {crc_endian}")
        polys = {}
        for name, inner in frames.items():
            M = int.from_bytes(inner[0:16], 'big')
            if crc_endian == "le":
                CRC = int.from_bytes(inner[16:20], 'little')
            else:
                CRC = int.from_bytes(inner[16:20], 'big')
            combined = (M << 32) ^ CRC
            polys[name] = combined

        # Take specific diffs
        pairs = [
            ("crc_heater_on", "crc_heater_off"),
            ("crc_blower_on", "crc_blower_off"),
            ("crc_temp_up_4", "crc_temp_down_1"),
            ("crc_temp_down_1", "crc_temp_down_2"),
            ("crc_pump_off_to_low", "crc_pump_high_to_off"),
            ("crc_light_on", "crc_heater_on"),
        ]

        diffs = []
        for a, b in pairs:
            if a in polys and b in polys:
                d = polys[a] ^ polys[b]
                diffs.append(d)
                print(f"    {a} ^ {b}: degree={gf2_degree(d)}")

        if len(diffs) >= 2:
            g = diffs[0]
            for d in diffs[1:]:
                prev_deg = gf2_degree(g)
                g = gf2_gcd(g, d)
                new_deg = gf2_degree(g)
                print(f"    GCD progress: {prev_deg} -> {new_deg}")
            print(f"    Final GCD degree: {gf2_degree(g)}")
            if gf2_degree(g) >= 32:
                print(f"    GCD = 0x{g:X}")

    # ─── 2. Try treating full 20 bytes as the CRC'd polynomial ───
    print("\n--- 2. Full 20-byte polynomial ---")
    # If CRC(M) appended to M makes M||CRC divisible by P,
    # then int.from_bytes(inner[0:20], 'big') is divisible by P
    # (for init=0, xor_out=0)
    # For nonzero init/xor_out, the residual is constant
    # So diffs are still divisible

    all_names = list(frames.keys())
    # Dedup
    seen_inner = {}
    unique_names = []
    for n in all_names:
        key = frames[n].hex()
        if key not in seen_inner:
            seen_inner[key] = n
            unique_names.append(n)

    all_polys = [int.from_bytes(frames[n], 'big') for n in unique_names]
    all_diffs = [all_polys[i] ^ all_polys[0] for i in range(1, len(all_polys)) if all_polys[i] != all_polys[0]]

    print(f"  {len(all_diffs)} diffs from {len(unique_names)} unique frames")

    if all_diffs:
        g = all_diffs[0]
        for i, d in enumerate(all_diffs[1:], 1):
            prev = gf2_degree(g)
            g = gf2_gcd(g, d)
            new = gf2_degree(g)
            if new != prev:
                print(f"    After diff {i+1}: degree {prev} -> {new}")
        final_deg = gf2_degree(g)
        print(f"    Final GCD degree: {final_deg}")
        if final_deg >= 32:
            print(f"    GCD = 0x{g:X}")
            # Try factoring
            for fd in range(1, 20):
                for factor in range(2, 1 << (fd + 1)):
                    if gf2_degree(factor) != fd: continue
                    q, r = gf2_divmod(g, factor)
                    if r == 0 and gf2_degree(q) == 32:
                        print(f"    P = 0x{q:X} (factored by 0x{factor:X})")

    # ─── 3. Brute force the polynomial from shift^32 relationship ───
    print("\n--- 3. Brute force from shift^32 constraint ---")
    # We know:
    # C(byte14, bit1) = 0x03B1590E
    # C(byte10, bit2) = 0x399CBDF3 (but could also be bit3)
    # C(byte10, bit3) = 0x73397BE6
    #
    # byte14 bit1 is at some distance D from byte10 bit2
    # In MSB-first CRC: D = (byte10 to byte14 = 4 bytes = 32 bits) + (bit2 - bit1 = 1 bit) = 33
    # So shift^33(C_byte14_bit1) should give C_byte10_bit2
    # But we need to try different models

    C14_b1_LE = 0x03B1590E
    C10_b2_LE = 0x399CBDF3
    C10_b3_LE = 0x73397BE6

    # The shift operation in the CRC register:
    # new = (old << 1) ^ (poly if old & 0x80000000 else 0) [Non-reflected]
    # After 33 shifts starting from C14_b1 should give C10_b2
    # After 34 shifts should give C10_b3

    # For the shift sequence: first, trace how many bits we can do without poly:
    # C14_b1 = 0x03B1590E, MSB = 0
    # After 5 shifts: 0x762B21C0, MSB = 0
    # After 6 shifts: 0xEC564380, MSB = 1 → feedback needed

    print(f"  C(14,1) = 0x{C14_b1_LE:08X}")
    print(f"  C(10,2) = 0x{C10_b2_LE:08X}")
    print(f"  Need: shift^33(C14_b1, P) = C10_b2")

    # After 6 free shifts: c = 0xEC564380
    # Step 7: c = (0xEC564380 << 1 = 0xD8AC8700) ^ P
    # Then 26 more shifts with P
    # Total: 7 + 26 = 33 shifts → should give C10_b2 = 0x399CBDF3

    # Let c7 = 0xD8AC8700 ^ P
    # Then shift^26(c7, P) = 0x399CBDF3
    # We can express this as a system of equations.

    # Alternative: try each of 2^32 polys? Too slow.
    # But we can narrow down. After step 7:
    # c7 = 0xD8AC8700 ^ P

    # After step 8: if MSB(c7) = 1, c8 = (c7 << 1) ^ P; else c8 = c7 << 1
    # MSB(c7) = MSB(0xD8AC8700 ^ P) = bit31(0xD8AC8700 ^ P)
    # 0xD8AC8700 bit 31 = 1 (0xD... > 0x8...). So MSB(c7) = 1 ^ bit31(P)
    # For standard CRC-32, the polynomial has bit 31 set (x^31 + ...), so MSB = 1.
    # Actually, the polynomial P in the table form excludes x^32, so P has bits 0-31.
    # The MSB (bit 31) of P depends on the specific polynomial.

    # Let me try a much smarter approach: solve bit by bit.
    # The shift operation is linear in GF(2):
    # shift(c) = (c << 1) ^ (P * msb(c))
    # where msb(c) = c >> 31

    # For 33 shifts from C14_b1 to C10_b2:
    # This is a linear function of P. Actually no, msb(c) depends on P through
    # all previous shifts, so it's complex.

    # Let me try ALL standard CRC-32 polynomials (Koopman list has a few hundred)
    # plus try constructing from the constraint.

    # Actually, let me just try a different approach: exhaustive over distance D.
    # Maybe the distance is NOT 33 bits. Maybe the CRC covers fewer bytes,
    # or the byte order is reversed, or there's padding.

    # Let's try all distances from 1 to 128:
    found_polys = {}
    for D in range(1, 129):
        # For distance D shifts: shift^D(C14_b1) = C10_b2
        # Start from C14_b1, shift D times, check if we reach C10_b2
        # For each possible P, this is deterministic, but 2^32 is too many.

        # Instead, use the algebraic constraint:
        # C10_b2 = C14_b1 * x^D mod (x^32 + P)
        # So (x^32 + P) divides (C14_b1 * x^D + C10_b2)
        # Also (x^32 + P) divides (C14_b1 * x^(D+1) + C10_b3)

        val1 = (C14_b1_LE << D) ^ C10_b2_LE
        val2 = (C14_b1_LE << (D + 1)) ^ C10_b3_LE

        g = gf2_gcd(val1, val2)
        deg = gf2_degree(g)
        if deg == 32:
            P_candidate = g & 0xFFFFFFFF
            # Verify: shift^D(C14_b1, P_candidate) should give C10_b2
            c = C14_b1_LE
            for _ in range(D):
                msb = (c >> 31) & 1
                c = ((c << 1) & 0xFFFFFFFF) ^ (P_candidate * msb)
            if c == C10_b2_LE:
                print(f"  ★ D={D}: P = 0x{P_candidate:08X}, shift verified!")
                found_polys[D] = P_candidate
            else:
                # Try with leading bit: P_full = (1 << 32) | P_candidate
                # Maybe we need the leading bit for the mod operation
                pass
        elif 32 < deg <= 40:
            # Try factoring
            for factor in range(2, 256):
                q, r = gf2_divmod(g, factor)
                if r == 0 and gf2_degree(q) == 32:
                    P_candidate = q & 0xFFFFFFFF
                    c = C14_b1_LE
                    for _ in range(D):
                        msb = (c >> 31) & 1
                        c = ((c << 1) & 0xFFFFFFFF) ^ (P_candidate * msb)
                    if c == C10_b2_LE:
                        print(f"  ★ D={D}: P = 0x{P_candidate:08X} (factored by 0x{factor:X}), verified!")
                        found_polys[D] = P_candidate

    if not found_polys:
        print("  No polynomial found for any distance 1-128")
        # Try more distances or with reflected C values
        print("\n  Trying with CRC_BE representation...")
        C14_b1 = 0x0E59B103  # byte-swapped
        C10_b2 = 0xF3BD9C39
        C10_b3 = 0xE67B3973

        # Check if doubling holds in BE representation
        print(f"  C14_b1_BE = 0x{C14_b1:08X}")
        print(f"  C14_b1_BE << 1 = 0x{(C14_b1 << 1) & 0xFFFFFFFF:08X}")
        C14_b2_BE = int.from_bytes(bytes.fromhex("0762B21C")[::-1], 'big')
        print(f"  C14_b2_BE = 0x{C14_b2_BE:08X}")
        # Doubling doesn't hold in BE. Confirms LE is the right representation.

    # ─── 4. Full frame poly verification ───
    if found_polys:
        print("\n--- 4. Full frame verification ---")
        for D, P in found_polys.items():
            # Determine message range from D
            # D = distance between byte[14]bit1 and byte[10]bit2 in shift register
            # For standard MSB-first over N bytes:
            # C(14, 1) = x^(shift_after_bit_14_1) and C(10, 2) = x^(shift_after_bit_10_2)
            # D = shift(10,2) - shift(14,1) = ((15-10)*8 + 2) - ((15-14)*8 + 1) = 42 - 9 = 33

            print(f"\n  D={D}, P=0x{P:08X}")
            print(f"  Reflected P = 0x{reflect_bits(P, 32):08X}")

            # Try as non-reflected CRC
            table = make_crc_table(P, reflected=False)
            for msg_start in range(8):
                for msg_end in [16, 17]:
                    for init in [0x00000000, 0xFFFFFFFF]:
                        for endian in ["le", "be"]:
                            msg0 = frames[unique_names[0]][msg_start:msg_end]
                            crc0_bytes = frames[unique_names[0]][16:20]
                            target0 = int.from_bytes(crc0_bytes, "little" if endian == "le" else "big")
                            raw0 = compute_crc(msg0, table, init, 0, False)
                            xor_out = raw0 ^ target0

                            ok = sum(1 for n in unique_names
                                     if compute_crc(frames[n][msg_start:msg_end], table, init, xor_out, False)
                                     == int.from_bytes(frames[n][16:20], "little" if endian == "le" else "big"))
                            if ok >= 5:
                                print(f"    [{msg_start}:{msg_end}] init=0x{init:08X} xout=0x{xor_out:08X} "
                                      f"[{endian}] unrefl: {ok}/{len(unique_names)}")

            # Try as reflected CRC
            P_ref = reflect_bits(P, 32)
            table_ref = make_crc_table(P_ref, reflected=True)
            for msg_start in range(8):
                for msg_end in [16, 17]:
                    for init in [0x00000000, 0xFFFFFFFF]:
                        for endian in ["le", "be"]:
                            msg0 = frames[unique_names[0]][msg_start:msg_end]
                            target0 = int.from_bytes(frames[unique_names[0]][16:20],
                                                     "little" if endian == "le" else "big")
                            raw0 = compute_crc(msg0, table_ref, init, 0, True)
                            xor_out = raw0 ^ target0

                            ok = sum(1 for n in unique_names
                                     if compute_crc(frames[n][msg_start:msg_end], table_ref, init, xor_out, True)
                                     == int.from_bytes(frames[n][16:20],
                                                       "little" if endian == "le" else "big"))
                            if ok >= 5:
                                print(f"    [{msg_start}:{msg_end}] init=0x{init:08X} xout=0x{xor_out:08X} "
                                      f"[{endian}] refl: {ok}/{len(unique_names)}")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()

