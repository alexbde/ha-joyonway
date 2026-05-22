#!/usr/bin/env python3
"""
Verify CRC-32 with 32-bit word byte-swap of message.

The brute force found P=0x04C11DB7 with constraints that imply:
- inner[10] is processed BEFORE inner[9] (swapped within a 32-bit word)
- inner[10] to inner[14] distance = 33 (matching word-swapped positions)

Hypothesis: CRC is computed on inner[0:16] with each 32-bit word byte-reversed.
"""
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

def word_swap(data):
    """Byte-reverse each 32-bit word."""
    result = bytearray()
    for i in range(0, len(data), 4):
        word = data[i:i+4]
        result.extend(reversed(word))
    return bytes(result)

def word_swap_16(data):
    """Byte-reverse each 16-bit word."""
    result = bytearray()
    for i in range(0, len(data), 2):
        word = data[i:i+2]
        result.extend(reversed(word))
    return bytes(result)


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    frames = []
    seen = set()
    for name, hex_str in data["frames"].items():
        inner = parse_frame(hex_str)
        key = inner.hex()
        if key not in seen:
            seen.add(key)
            frames.append({"name": name, "inner": inner})

    print(f"Testing {len(frames)} unique frames")
    print("=" * 70)

    POLY_NORMAL = 0x04C11DB7
    POLY_REFLECTED = 0xEDB88320

    # Try various message transformations
    transforms = [
        ("word32_swap", word_swap),
        ("word16_swap", word_swap_16),
        ("full_reverse", lambda d: bytes(reversed(d))),
        ("identity", lambda d: d),
    ]

    for xform_name, xform_fn in transforms:
        for poly, reflected, poly_name in [
            (POLY_NORMAL, False, "normal"),
            (POLY_REFLECTED, True, "reflected"),
        ]:
            table = make_table(poly, reflected)
            for msg_start in range(8):
                for msg_end in [16]:  # Focus on full payload first
                    for crc_endian in ["le", "be"]:
                        for init in [0x00000000, 0xFFFFFFFF]:
                            # Transform message
                            inner0 = frames[0]["inner"]
                            msg0 = xform_fn(inner0[msg_start:msg_end])
                            crc_bytes = inner0[16:20]
                            target0 = int.from_bytes(crc_bytes,
                                                     "little" if crc_endian == "le" else "big")
                            raw0 = compute_crc(msg0, table, init, 0, reflected)
                            xor_out = raw0 ^ target0

                            ok = 0
                            for f in frames:
                                msg = xform_fn(f["inner"][msg_start:msg_end])
                                actual = int.from_bytes(f["inner"][16:20],
                                                       "little" if crc_endian == "le" else "big")
                                computed = compute_crc(msg, table, init, xor_out, reflected)
                                if computed == actual:
                                    ok += 1

                            if ok >= 3:
                                print(f"  {ok}/{len(frames)} [{xform_name}] [{poly_name}] "
                                      f"msg[{msg_start}:{msg_end}] [{crc_endian}] "
                                      f"init=0x{init:08X} xout=0x{xor_out:08X}")

                            if ok == len(frames):
                                print(f"\n  ★★★ FULL MATCH! ★★★\n")
                                result = {
                                    "polynomial": f"0x{poly:08X}",
                                    "poly_name": poly_name,
                                    "init": f"0x{init:08X}",
                                    "xor_out": f"0x{xor_out:08X}",
                                    "reflected": reflected,
                                    "transform": xform_name,
                                    "msg_start": msg_start,
                                    "msg_end": msg_end,
                                    "crc_endian": crc_endian,
                                }
                                out_path = os.path.join(base, "..", "docs", "crc_params.json")
                                with open(out_path, "w") as fp:
                                    json.dump(result, fp, indent=2)
                                print(f"  Saved to {out_path}")
                                return

    # Also try partial ranges with word_swap
    print("\n--- Testing word32_swap with various ranges ---")
    table_n = make_table(POLY_NORMAL, False)
    table_r = make_table(POLY_REFLECTED, True)

    for msg_start in range(0, 12):
        for msg_end in range(msg_start + 4, 17, 4):  # Word-aligned
            for poly, table, reflected, pname in [
                (POLY_NORMAL, table_n, False, "N"),
                (POLY_REFLECTED, table_r, True, "R"),
            ]:
                for crc_endian in ["le", "be"]:
                    for init in [0x00000000, 0xFFFFFFFF]:
                        inner0 = frames[0]["inner"]
                        raw_msg = inner0[msg_start:msg_end]
                        if len(raw_msg) % 4 != 0:
                            continue
                        msg0 = word_swap(raw_msg)
                        target0 = int.from_bytes(inner0[16:20],
                                                 "little" if crc_endian == "le" else "big")
                        raw0 = compute_crc(msg0, table, init, 0, reflected)
                        xor_out = raw0 ^ target0

                        ok = sum(1 for f in frames
                                 if compute_crc(word_swap(f["inner"][msg_start:msg_end]),
                                                table, init, xor_out, reflected)
                                 == int.from_bytes(f["inner"][16:20],
                                                   "little" if crc_endian == "le" else "big"))
                        if ok >= 3:
                            print(f"  {ok}/{len(frames)} [ws32] [{pname}] "
                                  f"msg[{msg_start}:{msg_end}] [{crc_endian}] "
                                  f"init=0x{init:08X} xout=0x{xor_out:08X}")
                        if ok == len(frames):
                            print(f"\n  ★★★ FULL MATCH! ★★★")
                            return

    # Try CRC on the FULL inner (including CRC bytes) - maybe it's a check value
    print("\n--- Testing CRC over full inner (payload + CRC = 0 residual?) ---")
    for poly, table, reflected, pname in [
        (POLY_NORMAL, table_n, False, "N"),
        (POLY_REFLECTED, table_r, True, "R"),
    ]:
        for xform_name, xform_fn in transforms:
            for init in [0x00000000, 0xFFFFFFFF]:
                residuals = set()
                for f in frames:
                    msg = xform_fn(f["inner"][0:20])
                    res = compute_crc(msg, table, init, 0, reflected)
                    residuals.add(res)
                if len(residuals) == 1:
                    r = list(residuals)[0]
                    print(f"  CONSTANT RESIDUAL! [{pname}] [{xform_name}] init=0x{init:08X} "
                          f"residual=0x{r:08X}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()

