#!/usr/bin/env python3
"""
Verify CRC-32 polynomial 0x04C11DB7 against captured frames.
Try all combinations of:
  - Message byte range
  - Init value (0x00000000, 0xFFFFFFFF, and derived)
  - XorOut (derived from first frame)
  - Reflected vs non-reflected
  - CRC endianness (LE/BE)
  - Byte reversal in message
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
    return unescape(raw[1:-1])

def make_table(poly, reflected):
    table = []
    for i in range(256):
        crc = i if reflected else i << 24
        for _ in range(8):
            if reflected:
                crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            else:
                crc = ((crc << 1) & 0xFFFFFFFF) ^ poly if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
        table.append(crc)
    return table

def compute_crc(data, table, init, xor_out, reflected):
    crc = init
    for byte in data:
        if reflected:
            crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
        else:
            crc = ((crc << 8) & 0xFFFFFFFF) ^ table[((crc >> 24) ^ byte) & 0xFF]
    return crc ^ xor_out


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    # Parse frames, dedup
    frames = []
    seen = set()
    for name, hex_str in data["frames"].items():
        inner = parse_frame(hex_str)
        key = inner.hex()
        if key not in seen:
            seen.add(key)
            frames.append({"name": name, "inner": inner})

    print(f"Testing {len(frames)} unique frames with polynomial 0x04C11DB7")
    print("=" * 70)

    # The polynomial: normal = 0x04C11DB7, reflected = 0xEDB88320
    POLY_NORMAL = 0x04C11DB7
    POLY_REFLECTED = 0xEDB88320

    configs = [
        ("CRC-32 normal", POLY_NORMAL, False),
        ("CRC-32 reflected", POLY_REFLECTED, True),
    ]

    best_hits = 0
    best_config = None

    for crc_name, poly, reflected in configs:
        table = make_table(poly, reflected)

        # Try various message ranges
        for msg_start in range(8):
            for msg_end in range(msg_start + 4, 17):
                # Try different byte orderings
                for reverse_bytes in [False, True]:
                    for crc_endian in ["le", "be"]:
                        # Try common init values
                        for init in [0x00000000, 0xFFFFFFFF]:
                            # Derive xor_out from first frame
                            inner = frames[0]["inner"]
                            msg = inner[msg_start:msg_end]
                            if reverse_bytes:
                                msg = bytes(reversed(msg))
                            crc_bytes = inner[16:20]
                            target = int.from_bytes(crc_bytes,
                                                    "little" if crc_endian == "le" else "big")
                            raw_crc = compute_crc(msg, table, init, 0, reflected)
                            xor_out = raw_crc ^ target

                            # Verify against all frames
                            ok = 0
                            for f in frames:
                                msg = f["inner"][msg_start:msg_end]
                                if reverse_bytes:
                                    msg = bytes(reversed(msg))
                                crc_bytes = f["inner"][16:20]
                                actual = int.from_bytes(crc_bytes,
                                                       "little" if crc_endian == "le" else "big")
                                computed = compute_crc(msg, table, init, xor_out, reflected)
                                if computed == actual:
                                    ok += 1

                            if ok > best_hits:
                                best_hits = ok
                                best_config = {
                                    "name": crc_name,
                                    "msg_range": f"[{msg_start}:{msg_end}]",
                                    "reverse": reverse_bytes,
                                    "crc_endian": crc_endian,
                                    "init": init,
                                    "xor_out": xor_out,
                                    "reflected": reflected,
                                    "poly": poly,
                                }

                            if ok >= 5:
                                rev_str = "rev" if reverse_bytes else "fwd"
                                print(f"  {ok}/{len(frames)} [{crc_name}] msg{msg_start}:{msg_end} "
                                      f"[{rev_str}] [{crc_endian}] init=0x{init:08X} "
                                      f"xout=0x{xor_out:08X}")

                            if ok == len(frames):
                                print(f"\n{'='*70}")
                                print(f"  ★★★ FULL MATCH! CRC CRACKED! ★★★")
                                print(f"{'='*70}")
                                print(f"  Algorithm: {crc_name}")
                                print(f"  Polynomial: 0x{poly:08X}")
                                print(f"  Init: 0x{init:08X}")
                                print(f"  XorOut: 0x{xor_out:08X}")
                                print(f"  Reflected: {reflected}")
                                print(f"  Message range: inner[{msg_start}:{msg_end}]")
                                print(f"  Byte order: {'reversed' if reverse_bytes else 'normal'}")
                                print(f"  CRC endian: {crc_endian}")
                                print(f"{'='*70}\n")

                                # Save to file
                                result = {
                                    "polynomial": f"0x{poly:08X}",
                                    "init": f"0x{init:08X}",
                                    "xor_out": f"0x{xor_out:08X}",
                                    "reflected": reflected,
                                    "msg_start": msg_start,
                                    "msg_end": msg_end,
                                    "reverse_bytes": reverse_bytes,
                                    "crc_endian": crc_endian,
                                    "verified_frames": ok,
                                    "total_frames": len(frames),
                                }
                                out_path = os.path.join(base, "..", "docs", "crc_params.json")
                                with open(out_path, "w") as fp:
                                    json.dump(result, fp, indent=2)
                                print(f"  Saved to {os.path.abspath(out_path)}")

    # Also try with individual byte reflection (reflect each msg byte)
    print("\n--- Trying with per-byte reflection ---")
    for poly, reflected, crc_name in [(POLY_NORMAL, False, "normal+byteref"),
                                       (POLY_REFLECTED, True, "reflected+byteref")]:
        table = make_table(poly, reflected)
        for msg_start in range(8):
            for msg_end in range(msg_start + 4, 17):
                for crc_endian in ["le", "be"]:
                    for init in [0x00000000, 0xFFFFFFFF]:
                        inner = frames[0]["inner"]
                        msg = bytes(int(f'{b:08b}'[::-1], 2) for b in inner[msg_start:msg_end])
                        crc_bytes = inner[16:20]
                        target = int.from_bytes(crc_bytes,
                                                "little" if crc_endian == "le" else "big")
                        raw_crc = compute_crc(msg, table, init, 0, reflected)
                        xor_out = raw_crc ^ target

                        ok = 0
                        for f in frames:
                            msg = bytes(int(f'{b:08b}'[::-1], 2) for b in f["inner"][msg_start:msg_end])
                            actual = int.from_bytes(f["inner"][16:20],
                                                   "little" if crc_endian == "le" else "big")
                            if compute_crc(msg, table, init, xor_out, reflected) == actual:
                                ok += 1
                        if ok >= 5:
                            print(f"  {ok}/{len(frames)} [{crc_name}] msg{msg_start}:{msg_end} "
                                  f"[{crc_endian}] init=0x{init:08X} xout=0x{xor_out:08X}")
                        if ok == len(frames):
                            print(f"\n  ★★★ FULL MATCH WITH BYTE REFLECTION! ★★★")
                            print(f"  Poly=0x{poly:08X} refl={reflected} "
                                  f"msg[{msg_start}:{msg_end}] {crc_endian} "
                                  f"init=0x{init:08X} xout=0x{xor_out:08X}")

    print(f"\nBest result: {best_hits}/{len(frames)} frames matched")
    if best_config:
        print(f"  Config: {best_config}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

