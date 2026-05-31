#!/usr/bin/env python3
"""
CRC-32 polynomial extraction via GF(2) polynomial GCD.

For a CRC-32 with polynomial P(x):
  M(x) * x^32 + CRC(x) = 0 mod P(x)

So P(x) divides (M(x) * x^32 + CRC(x)) for every message/CRC pair.
The GCD of all such values across multiple pairs converges to P(x).

This avoids brute-force entirely — it's algebraic extraction.
"""
from __future__ import annotations

import json
import os


ESCAPE_MAP = {0x11: 0x1A, 0x0B: 0x1B, 0x13: 0x1C, 0x14: 0x1D, 0x15: 0x1E}


def unescape(data: bytes) -> bytes:
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


def parse_frame(hex_str: str) -> bytes:
    raw = bytes.fromhex(hex_str)
    inner = unescape(raw[1:-1])
    return bytes([raw[0]]) + inner + bytes([raw[-1]])


# --- GF(2) polynomial arithmetic ---

def gf2_degree(a: int) -> int:
    return a.bit_length() - 1 if a else -1


def gf2_mod(a: int, b: int) -> int:
    """a mod b in GF(2)[x]"""
    db = gf2_degree(b)
    if db < 0:
        raise ZeroDivisionError
    while True:
        da = gf2_degree(a)
        if da < db:
            return a
        a ^= b << (da - db)


def gf2_gcd(a: int, b: int) -> int:
    """GCD(a, b) in GF(2)[x]"""
    while b:
        a, b = b, gf2_mod(a, b)
    return a


def reflect_byte(b: int) -> int:
    """Reverse bits of a single byte."""
    r = 0
    for i in range(8):
        if b & (1 << i):
            r |= 1 << (7 - i)
    return r


def reflect_bits(value: int, width: int) -> int:
    """Reverse bits of a value."""
    r = 0
    for i in range(width):
        if value & (1 << i):
            r |= 1 << (width - 1 - i)
    return r


def bytes_to_poly(data: bytes, reflect: bool = False) -> int:
    """Convert bytes to GF(2) polynomial integer."""
    if reflect:
        # Reflect each byte individually
        return int.from_bytes(bytes(reflect_byte(b) for b in data), "big")
    return int.from_bytes(data, "big")


def make_crc_table(poly: int, reflected: bool) -> list[int]:
    """Build CRC-32 lookup table."""
    table = []
    if reflected:
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ poly
                else:
                    crc >>= 1
            table.append(crc)
    else:
        for i in range(256):
            crc = i << 24
            for _ in range(8):
                if crc & 0x80000000:
                    crc = ((crc << 1) & 0xFFFFFFFF) ^ poly
                else:
                    crc = (crc << 1) & 0xFFFFFFFF
            table.append(crc)
    return table


def compute_crc(data: bytes, table: list[int], init: int, xor_out: int,
                reflected: bool) -> int:
    crc = init
    if reflected:
        for byte in data:
            crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    else:
        for byte in data:
            crc = ((crc << 8) & 0xFFFFFFFF) ^ table[((crc >> 24) ^ byte) & 0xFF]
    return crc ^ xor_out


def try_extract_poly(frames: list[dict], msg_start: int, msg_end: int,
                     crc_endian: str, reflect_input: bool,
                     use_wire: bool = False) -> int | None:
    """Try to extract the CRC polynomial for given configuration."""

    # Build (M(x) * x^32 + CRC(x)) for each frame, then take DIFFERENCES
    # The XOR of two such values eliminates init/xor_out constants,
    # giving a polynomial that's a multiple of P(x).
    polys = []
    for f in frames:
        if use_wire:
            wire = f.get("wire_inner")
            if wire is None:
                return None
            msg_bytes = wire[msg_start:msg_end]
            crc_bytes_raw = wire[-4:]
        else:
            msg_bytes = f["full"][msg_start:msg_end]
            crc_bytes_raw = f["full"][17:21]

        msg_poly = bytes_to_poly(msg_bytes, reflect=reflect_input)
        if crc_endian == "le":
            crc_val = int.from_bytes(crc_bytes_raw, "little")
        else:
            crc_val = int.from_bytes(crc_bytes_raw, "big")

        if reflect_input:
            crc_val = reflect_bits(crc_val, 32)

        combined = (msg_poly << 32) ^ crc_val
        polys.append(combined)

    # XOR each with the first to get differences (multiples of P)
    diffs = [polys[i] ^ polys[0] for i in range(1, len(polys))]
    diffs = [d for d in diffs if d != 0]  # skip if identical

    if len(diffs) < 2:
        return None

    # Compute GCD of all difference polynomials
    result = diffs[0]
    for p in diffs[1:]:
        result = gf2_gcd(result, p)
        if gf2_degree(result) < 32:
            return None

    # The GCD should be the polynomial (degree 32) or a multiple
    deg = gf2_degree(result)
    if deg == 32:
        return result  # This IS the polynomial (with leading 1)
    elif deg > 32:
        # Try to factor out: result might be poly * some_factor
        # For CRC-32, poly has degree 32. Try dividing by small factors.
        # Or just return None for now.
        return None
    return None


def verify_poly(poly: int, frames: list[dict], msg_start: int, msg_end: int,
                crc_endian: str, reflected: bool) -> tuple[int, int, int] | None:
    """Given a polynomial, find init and xor_out that match all frames."""

    # For CRC-32 poly (degree 32), strip the leading bit for the table
    poly_val = poly & 0xFFFFFFFF  # Remove x^32 term

    if reflected:
        poly_reflected = reflect_bits(poly_val, 32)
        table = make_crc_table(poly_reflected, reflected=True)
    else:
        table = make_crc_table(poly_val, reflected=False)

    # Use first frame to determine init/xor_out
    for init in [0x00000000, 0xFFFFFFFF]:
        f0 = frames[0]
        msg = f0["full"][msg_start:msg_end]
        crc_bytes = f0["full"][17:21]

        if crc_endian == "le":
            target = int.from_bytes(crc_bytes, "little")
        else:
            target = int.from_bytes(crc_bytes, "big")

        raw = compute_crc(msg, table, init, 0, reflected)
        xor_out = raw ^ target

        # Verify against ALL frames
        ok = True
        for f in frames[1:]:
            msg = f["full"][msg_start:msg_end]
            crc_bytes = f["full"][17:21]
            if crc_endian == "le":
                target = int.from_bytes(crc_bytes, "little")
            else:
                target = int.from_bytes(crc_bytes, "big")
            computed = compute_crc(msg, table, init, xor_out, reflected)
            if computed != target:
                ok = False
                break

        if ok:
            return init, xor_out, poly_reflected if reflected else poly_val

    # Try deriving init from the algebra
    # CRC(msg) = linear(msg) ^ f(init) ^ xor_out
    # Two frames: CRC1 ^ CRC2 = linear(msg1) ^ linear(msg2) (init and xor_out cancel)
    # This is independent of init, which we already exploited via GCD.
    # Just try more init values.
    for init in range(256):  # Try low init values
        f0 = frames[0]
        msg = f0["full"][msg_start:msg_end]
        crc_bytes = f0["full"][17:21]
        if crc_endian == "le":
            target = int.from_bytes(crc_bytes, "little")
        else:
            target = int.from_bytes(crc_bytes, "big")
        raw = compute_crc(msg, table, init, 0, reflected)
        xor_out = raw ^ target

        ok = True
        for f in frames[1:5]:  # Quick check first 5
            msg = f["full"][msg_start:msg_end]
            crc_bytes = f["full"][17:21]
            if crc_endian == "le":
                target = int.from_bytes(crc_bytes, "little")
            else:
                target = int.from_bytes(crc_bytes, "big")
            if compute_crc(msg, table, init, xor_out, reflected) != target:
                ok = False
                break
        if ok:
            # Full verify
            all_ok = all(
                compute_crc(f["full"][msg_start:msg_end], table, init, xor_out, reflected)
                == (int.from_bytes(f["full"][17:21], "little" if crc_endian == "le" else "big"))
                for f in frames
            )
            if all_ok:
                return init, xor_out, poly_reflected if reflected else poly_val

    return None


def main():
    import argparse
    parser = argparse.ArgumentParser(description="CRC polynomial extraction")
    parser.add_argument("--input", help="Path to crc_session.json from capture_crc_session.py")
    args = parser.parse_args()

    base = os.path.dirname(__file__)

    all_hex = {}

    if args.input:
        # Load same-session frames from CRC capture tool
        with open(args.input) as f:
            crc_data = json.load(f)
        all_hex = dict(crc_data.get("frames", {}))
        print(f"  Loaded {len(all_hex)} same-session frames from {args.input}")
    else:
        # Legacy mode: load from separate capture files
        with open(os.path.join(base, "captures_temp", "temp_commands.json")) as f:
            temp_data = json.load(f)
        all_hex["light_toggle"] = "1a0120103ca110a10000404000c00056003031eeb21d"
        all_hex["pump_off_low"] = "1a0120103ca110a10202000000c00056007dd2146b1d"
        all_hex["pump_low_high"] = "1a0120103ca110a10604000000c0005600fc1221c61d"
        all_hex["pump_high_off"] = "1a0120103ca110a10400000000c0005600735738e91d"
        for k, v in temp_data.items():
            if k.endswith("F"):
                all_hex[f"temp_{k}"] = v

    frames = []
    for name, hex_str in all_hex.items():
        f = parse_frame(hex_str)
        frames.append({"name": name, "full": f})

    print("=" * 70)
    print("  P25B85 CRC Polynomial Extraction (GF(2) GCD method)")
    print("=" * 70)
    print(f"\n  {len(frames)} frames loaded\n")

    # Also build "raw wire" versions (escaped, no start/end delimiters)
    for f in frames:
        raw_hex = [v for k, v in all_hex.items() if k == f["name"]][0] if f["name"] in all_hex else None
        if raw_hex:
            raw = bytes.fromhex(raw_hex)
            f["wire"] = raw  # includes 0x1A and 0x1D
            f["wire_inner"] = raw[1:-1]  # escaped payload + CRC, no delimiters

    # Try all combinations
    byte_ranges = [
        (1, 17, "bytes 1-16 (payload only)"),
        (0, 17, "bytes 0-16 (including 0x1A start)"),
        (1, 16, "bytes 1-15 (excluding last 0x00)"),
        (0, 16, "bytes 0-15"),
        (2, 17, "bytes 2-16 (skip dest)"),
        (0, 21, "bytes 0-20 (full frame incl CRC)"),
        (1, 21, "bytes 1-20 (payload + CRC)"),
        (8, 17, "bytes 8-16 (variable part only)"),
    ]

    found = []
    for use_wire in [False]:
        print(f"\n  --- Testing UNESCAPED bytes ---\n")
        ranges = byte_ranges

        for msg_start, msg_end, desc in ranges:
            for crc_endian in ["le", "be"]:
                for reflect in [False, True]:
                    ref_str = "reflected" if reflect else "normal"
                    poly = try_extract_poly(frames, msg_start, msg_end,
                                            crc_endian, reflect, False)
                    deg = gf2_degree(poly) if poly else -1
                    print(f"  [{desc}] [{crc_endian}] [{ref_str}]: degree={deg}")
                    if poly and gf2_degree(poly) == 32:
                        poly_val = poly & 0xFFFFFFFF
                        print(f"  POLY FOUND! [{desc}] [{crc_endian}] [{ref_str}]")
                        print(f"    Raw poly (with x^32): {poly:#012x}")
                        print(f"    Poly value: {poly_val:#010x}")
                        print(f"    Reflected:  {reflect_bits(poly_val, 32):#010x}")

                        # Try to find init and xor_out
                        result = verify_poly(poly, frames, msg_start, msg_end,
                                             crc_endian, reflect)
                        if result:
                            init, xor_out, table_poly = result
                            print(f"    Init:    {init:#010x}")
                            print(f"    XorOut:  {xor_out:#010x}")
                            print(f"    Table poly: {table_poly:#010x}")

                            # Full verification count
                            table = make_crc_table(table_poly, reflected=reflect)
                            ok = sum(
                                1 for f in frames
                                if compute_crc(f["full"][msg_start:msg_end], table,
                                               init, xor_out, reflect)
                                == int.from_bytes(f["full"][17:21],
                                                  "little" if crc_endian == "le" else "big")
                            )
                            print(f"    Verified: {ok}/{len(frames)} frames")
                            found.append({
                                "desc": desc,
                                "endian": crc_endian,
                                "reflected": reflect,
                                "poly": poly_val,
                                "init": init,
                                "xor_out": xor_out,
                                "table_poly": table_poly,
                                "verified": ok,
                                "range": (msg_start, msg_end),
                            })
                        else:
                            print(f"    (Could not determine init/xor_out)")
                        print()

    print("=" * 70)
    if found:
        best = max(found, key=lambda x: x["verified"])
        r = best
        print(f"  CRC ALGORITHM CRACKED!")
        print(f"")
        print(f"  Polynomial:  {r['poly']:#010x}")
        print(f"  Init:        {r['init']:#010x}")
        print(f"  XorOut:      {r['xor_out']:#010x}")
        print(f"  Reflected:   {r['reflected']}")
        print(f"  CRC endian:  {r['endian']}")
        print(f"  Byte range:  [{r['range'][0]}:{r['range'][1]}]")
        print(f"  Verified:    {r['verified']}/{len(frames)} frames")
        print()

        # Generate a test CRC
        table = make_crc_table(r["table_poly"], reflected=r["reflected"])
        test_frame = frames[0]
        msg = test_frame["full"][r["range"][0]:r["range"][1]]
        crc = compute_crc(msg, table, r["init"], r["xor_out"], r["reflected"])
        print(f"  Test: {test_frame['name']}")
        print(f"    Computed CRC: {crc:#010x}")
        if r["endian"] == "le":
            actual = int.from_bytes(test_frame["full"][17:21], "little")
        else:
            actual = int.from_bytes(test_frame["full"][17:21], "big")
        print(f"    Actual CRC:   {actual:#010x}")
        print(f"    Match: {crc == actual}")

        # Save results
        results = {
            "polynomial": f"{r['poly']:#010x}",
            "init": f"{r['init']:#010x}",
            "xor_out": f"{r['xor_out']:#010x}",
            "reflected": r["reflected"],
            "crc_endian": r["endian"],
            "byte_range_start": r["range"][0],
            "byte_range_end": r["range"][1],
            "verified_frames": r["verified"],
            "total_frames": len(frames),
        }
        results_path = os.path.join(base, "..", "docs", "crc_params.json")
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n  Parameters saved to {os.path.abspath(results_path)}")
    else:
        print("  No polynomial found with tested configurations.")
        print("  The CRC may use a non-standard byte range or pre-processing.")
    print("=" * 70)


if __name__ == "__main__":
    main()









