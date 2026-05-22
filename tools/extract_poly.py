#!/usr/bin/env python3
"""
Extract CRC-32 polynomial from per-bit contributions at two byte positions.

We know:
  C(byte14, bit0) = x^8 mod P = 0x01D8AC87   (8 bits from end of msg)
  C(byte10, bit2) = x^42 mod P = 0x399CBDF3   (42 bits from end)

So P divides (0x01D8AC87 * x^34 + 0x399CBDF3).

We need at least 2 independent divisibility constraints to find P via GCD.
This script derives all available constraints from captured bit-contributions
and finds the polynomial.
"""
import json, os, itertools

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
    return {"raw": raw, "inner": inner}

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

def reflect_bits(v, w):
    r = 0
    for i in range(w):
        if v & (1 << i): r |= 1 << (w - 1 - i)
    return r

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

def shift_n(val, n, poly32):
    """Shift val left n times in GF(2) mod (x^32 + poly32)."""
    p = (1 << 32) | poly32
    for _ in range(n):
        val <<= 1
        if val & (1 << 32):
            val ^= p
    return val & 0xFFFFFFFF


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    frames_raw = data["frames"]

    # Parse all frames
    all_frames = []
    for name, hex_str in frames_raw.items():
        f = parse_frame(hex_str)
        f["name"] = name
        f["payload"] = f["inner"][:16]
        f["crc_bytes"] = f["inner"][16:20]
        f["crc_le"] = int.from_bytes(f["crc_bytes"], "little")
        f["crc_be"] = int.from_bytes(f["crc_bytes"], "big")
        all_frames.append(f)

    # Dedup
    seen = {}
    unique = []
    for f in all_frames:
        key = f["inner"].hex()
        if key not in seen:
            seen[key] = f["name"]
            unique.append(f)

    a1 = [f for f in unique if f["inner"][4] == 0xA1]

    print("=" * 70)
    print("  CRC POLYNOMIAL EXTRACTION — Per-Bit Method")
    print("=" * 70)

    # ─── 1. Extract all per-bit contributions from 1-byte-diff pairs ───
    # bit_contributions[(byte_pos, bit)] = CRC_LE_XOR
    bit_contribs = {}

    for i in range(len(a1)):
        for j in range(i+1, len(a1)):
            fi, fj = a1[i], a1[j]
            pi, pj = fi["payload"], fj["payload"]
            diffs = [k for k in range(16) if pi[k] != pj[k]]
            if len(diffs) == 1:
                pos = diffs[0]
                bxor = pi[pos] ^ pj[pos]
                cxor = fi["crc_le"] ^ fj["crc_le"]
                # If single bit
                if bxor & (bxor - 1) == 0:
                    bit = bxor.bit_length() - 1
                    bit_contribs[(pos, bit)] = cxor

    print(f"\n  Extracted {len(bit_contribs)} single-bit CRC contributions:")
    for (pos, bit), cxor in sorted(bit_contribs.items()):
        print(f"    byte[{pos}] bit {bit}: CRC_LE = 0x{cxor:08X}")

    # Also extract multi-bit contributions for cross-checking
    multi_contribs = {}
    for i in range(len(a1)):
        for j in range(i+1, len(a1)):
            fi, fj = a1[i], a1[j]
            pi, pj = fi["payload"], fj["payload"]
            diffs = [k for k in range(16) if pi[k] != pj[k]]
            if len(diffs) == 1:
                pos = diffs[0]
                bxor = pi[pos] ^ pj[pos]
                cxor = fi["crc_le"] ^ fj["crc_le"]
                multi_contribs[(pos, bxor)] = cxor

    # ─── 2. Build polynomial divisibility equations ───
    # In a standard MSB-first CRC-32 over msg bytes [0:16]:
    #   C(pos, bit) = x^((15 - pos)*8 + bit) mod P
    # where bit 7 = MSB, bit 0 = LSB
    # The last byte (pos=15) bit 0 has x^0 mod P = 1 shift remaining
    # Actually: bit b of byte p contributes x^((15-p)*8 + (7-b)) if MSB-first
    # Wait, need to think carefully.

    # MSB-first CRC: process bit 7 first, so bit 7 of last byte gets
    # shifted 0 more times than bit 7, and bit 0 of last byte gets 0 total shifts.
    # Actually the contribution of each bit is how many MORE shifts it gets pushed through.
    # bit 7 of byte[15] is the SECOND-TO-LAST bit processed (bit 0 of byte[15] is last).
    # bit 0 of byte[15] gets 0 additional shifts → contribution = 1 (x^0)
    # bit 7 of byte[15] gets 7 additional shifts → x^7
    # bit 0 of byte[14] gets 8 additional shifts → x^8

    # So C(pos, bit) = x^((15-pos)*8 + bit) mod P
    # No wait: within a byte, bit 7 (MSB) is processed FIRST and gets shifted
    # through 7 more subsequent bits in the same byte plus all remaining bytes.
    # bit b at position pos: there are (15-pos) more bytes after, each 8 bits,
    # plus (b) more bits within the same byte after bit b.
    # Wait no. Within a byte processed MSB first:
    # bit 7 → then bit 6 → ... → bit 0
    # After processing bit 7, there are still 7 bits in the same byte and
    # (15-pos) * 8 bits in subsequent bytes = 7 + (15-pos)*8 more shifts.
    # After processing bit b, there are b more bits in the byte and (15-pos)*8
    # bits remaining = b + (15-pos)*8 more shifts.
    # So contribution = x^(b + (15-pos)*8) mod P. BUT WAIT:
    # The last bit processed (bit 0 of byte 15) has 0 remaining shifts.
    # b=0, pos=15: 0 + (15-15)*8 = 0. x^0 = 1. Correct.
    # bit 7 of byte 0: 7 + (15-0)*8 = 7 + 120 = 127. x^127 mod P.

    # So: C(pos, bit) in CRC_LE representation = x^(b + (15-pos)*8) mod P

    # For byte[14] bit 1: x^(1 + (15-14)*8) = x^9 mod P = 0x03B1590E
    # For byte[14] bit 2: x^(2 + 8) = x^10 mod P = 0x0762B21C
    # For byte[14] bit 3: x^(3 + 8) = x^11 mod P = 0x0EC56438
    # For byte[10] bit 2: x^(2 + (15-10)*8) = x^42 mod P = 0x399CBDF3
    # For byte[10] bit 3: x^(3 + 40) = x^43 mod P = 0x73397BE6

    # Verify doubling: x^10 should = x^9 * x
    print(f"\n  Doubling check:")
    if (14, 1) in bit_contribs and (14, 2) in bit_contribs:
        c1 = bit_contribs[(14, 1)]
        c2 = bit_contribs[(14, 2)]
        print(f"    byte[14] bit1 << 1 = 0x{(c1 << 1) & 0xFFFFFFFF:08X}, bit2 = 0x{c2:08X}: {'OK' if (c1 << 1) & 0xFFFFFFFF == c2 else 'MISMATCH'}")

    # ─── 3. Polynomial extraction using GF(2) GCD ───
    # For CRC_LE representation, if MSB-first:
    # x^n mod P = C(pos, bit) means (x^32 + P_low) divides (x^n - C)
    # In GF(2): (x^32 + P_low) | (x^n + C)  (since -1 = 1 in GF(2))
    #
    # Equation for each known C(pos, bit):
    #   (x^32 + P_low) divides (x^n + C)  where n = bit + (15-pos)*8
    #
    # So GCD of all (x^n + C) values should give (x^32 + P_low)

    print(f"\n--- Polynomial extraction via GCD of (x^n + C) ---")

    equations = []
    for (pos, bit), cval in sorted(bit_contribs.items()):
        n = bit + (15 - pos) * 8
        poly_val = (1 << n) ^ cval  # x^n + C
        equations.append((pos, bit, n, poly_val))
        print(f"  x^{n} mod P = 0x{cval:08X}  →  P | (x^{n} + 0x{cval:08X})  [byte[{pos}] bit {bit}]")

    if len(equations) >= 2:
        g = equations[0][3]
        for eq in equations[1:]:
            g = gf2_gcd(g, eq[3])
        deg = gf2_degree(g)
        print(f"\n  GCD of {len(equations)} equations: degree = {deg}")
        if deg >= 32:
            poly32 = g & 0xFFFFFFFF
            print(f"  Raw GCD = 0x{g:X}")
            print(f"  Bottom 32 bits = 0x{poly32:08X}")

            # Try to factor out to get exactly degree 32
            if deg > 32:
                # The GCD might be P * small_factor
                # Try dividing by small factors of the per-bit GCD
                working = g
                for factor_deg in range(1, deg - 31):
                    for factor in range(1 << factor_deg, 1 << (factor_deg + 1)):
                        q, r = gf2_divmod(working, factor)
                        if r == 0 and gf2_degree(q) == 32:
                            poly32 = q & 0xFFFFFFFF
                            print(f"  Factored: 0x{g:X} = 0x{q:X} * 0x{factor:X}")
                            print(f"  Polynomial (deg 32): 0x{q:X}")
                            print(f"  P_low = 0x{poly32:08X}")
                            working = q
                            break
                    if gf2_degree(working) == 32:
                        break

            # Also try with multi-bit contributions
            print(f"\n  Adding multi-bit equations to refine...")
            for (pos, bxor), cval in sorted(multi_contribs.items()):
                if bxor & (bxor - 1) == 0:
                    continue  # already handled as single-bit
                # Multi-bit: contribution is XOR of individual bit contributions
                # This gives us: P | (sum of x^n_i + C) for each set bit i
                multi_poly = cval
                for bit in range(8):
                    if bxor & (1 << bit):
                        n = bit + (15 - pos) * 8
                        multi_poly ^= (1 << n)
                g = gf2_gcd(g, multi_poly)
                deg = gf2_degree(g)

            print(f"  Refined GCD: degree = {deg}, value = 0x{g:X}")

            if deg > 32:
                working = g
                for factor in range(2, 1 << min(deg - 32 + 1, 16)):
                    q, r = gf2_divmod(working, factor)
                    if r == 0 and gf2_degree(q) == 32:
                        print(f"  Factored: P = 0x{q:X} (factor 0x{factor:X})")
                        working = q
                        break
                g = working

    # ─── 4. Also try LSB-first (reflected) model ───
    print(f"\n--- Trying LSB-first (reflected) model ---")
    # For reflected CRC, the contribution is different:
    # C(pos, bit) = x^((pos)*8 + (7-bit)) mod P_reflected
    # Where byte[0] is processed first (gets most shifts)
    # bit 0 (LSB) is processed first in reflected mode

    for model, calc_n in [
        ("MSB-first", lambda pos, bit: bit + (15 - pos) * 8),
        ("LSB-first", lambda pos, bit: (7 - bit) + pos * 8),
        ("MSB-first-rev", lambda pos, bit: (7 - bit) + (15 - pos) * 8),
        ("LSB-first-rev", lambda pos, bit: bit + pos * 8),
    ]:
        equations_m = []
        for (pos, bit), cval in sorted(bit_contribs.items()):
            n = calc_n(pos, bit)
            poly_val = (1 << n) ^ cval
            equations_m.append((n, poly_val))

        if len(equations_m) >= 2:
            g = equations_m[0][1]
            for _, pv in equations_m[1:]:
                g = gf2_gcd(g, pv)

            # Add multi-bit
            for (pos, bxor), cval in sorted(multi_contribs.items()):
                if bxor & (bxor - 1) == 0: continue
                mpoly = cval
                for bit in range(8):
                    if bxor & (1 << bit):
                        mpoly ^= (1 << calc_n(pos, bit))
                g = gf2_gcd(g, mpoly)

            deg = gf2_degree(g)
            if deg >= 32:
                print(f"  [{model}] GCD degree = {deg}, value = 0x{g:X}")
                if deg > 32:
                    for factor in range(2, 1 << min(deg - 32 + 1, 20)):
                        q, r = gf2_divmod(g, factor)
                        if r == 0 and gf2_degree(q) == 32:
                            print(f"    Factored: P = 0x{q:X} (factor 0x{factor:X})")
                            g = q
                            break
                if gf2_degree(g) == 32:
                    poly32 = g & 0xFFFFFFFF
                    print(f"    POLYNOMIAL FOUND: 0x{g:X}")
                    print(f"    P_low = 0x{poly32:08X}")
                    print(f"    Reflected = 0x{reflect_bits(poly32, 32):08X}")

    # ─── 5. Full frame GCD verification ───
    # If we found a polynomial, verify against all frames
    print(f"\n--- Full polynomial equations from all frame pairs ---")
    # For EVERY pair of frames, the diff is divisible by P:
    # (M_a * x^32 + CRC_a) XOR (M_b * x^32 + CRC_b) is divisible by P
    # Here M is the message polynomial (bytes [0:16] as big-endian polynomial)

    # Let's try this with the full inner content [0:20] as a single polynomial
    # If CRC check passes (residual is constant), then inner[0:20] as poly is ≡ const mod P
    # So diffs are divisible by P

    for endian in ["be"]:
        polys = []
        for f in unique:
            # Entire inner as a polynomial
            val = int.from_bytes(f["inner"], "big")
            polys.append(val)

        diffs = [polys[i] ^ polys[0] for i in range(1, len(polys)) if polys[i] != polys[0]]
        if len(diffs) >= 2:
            g = diffs[0]
            for d in diffs[1:]:
                g = gf2_gcd(g, d)
            deg = gf2_degree(g)
            print(f"  Full inner (all {len(unique)} unique, {endian}): GCD degree = {deg}")
            if deg >= 32:
                print(f"    GCD = 0x{g:X}")
                if deg > 32:
                    # Factor out
                    for f_bits in range(1, min(deg - 31, 16)):
                        for factor in range(1 << f_bits, 1 << (f_bits + 1)):
                            q, r = gf2_divmod(g, factor)
                            if r == 0 and gf2_degree(q) == 32:
                                print(f"    Factored: P = 0x{q:X}")
                                break

        # Also just A1 frames
        a1_polys = []
        for f in a1:
            val = int.from_bytes(f["inner"], "big")
            a1_polys.append(val)

        a1_diffs = [a1_polys[i] ^ a1_polys[0] for i in range(1, len(a1_polys)) if a1_polys[i] != a1_polys[0]]
        if len(a1_diffs) >= 2:
            g = a1_diffs[0]
            for d in a1_diffs[1:]:
                g = gf2_gcd(g, d)
            deg = gf2_degree(g)
            print(f"  A1 inner ({len(a1)} frames, {endian}): GCD degree = {deg}")
            if deg >= 32:
                print(f"    GCD = 0x{g:X}")

    # ─── 6. Try CRC on raw (escaped) wire bytes ───
    print(f"\n--- Full WIRE (escaped) polynomial GCD ---")
    # Maybe the CRC is computed BEFORE unescaping?
    wire_polys = []
    for f in unique:
        wire = f["raw"][1:-1]  # strip 0x1A and 0x1D delimiters
        val = int.from_bytes(wire, "big")
        wire_polys.append((f["name"], val, len(wire)))

    # Group by wire length
    wire_by_len = {}
    for name, val, wlen in wire_polys:
        wire_by_len.setdefault(wlen, []).append((name, val))

    for wlen, entries in sorted(wire_by_len.items()):
        if len(entries) < 3: continue
        polys = [v for _, v in entries]
        diffs = [polys[i] ^ polys[0] for i in range(1, len(polys)) if polys[i] != polys[0]]
        if len(diffs) < 2: continue
        g = diffs[0]
        for d in diffs[1:]:
            g = gf2_gcd(g, d)
        deg = gf2_degree(g)
        print(f"  Wire len={wlen} ({len(entries)} frames): GCD degree = {deg}")
        if deg >= 32:
            print(f"    GCD = 0x{g:X}")

    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()

