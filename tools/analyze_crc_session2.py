#!/usr/bin/env python3
"""Deep analysis of same-session CRC captures."""
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
    return {"raw": raw, "inner": inner, "raw_len": len(raw)}

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
def reflect_bits(v, w):
    r = 0
    for i in range(w):
        if v & (1 << i): r |= 1 << (w - 1 - i)
    return r
def reflect_byte(b): return reflect_bits(b, 8)
def bytes_to_poly(data, reflect=False):
    if reflect: return int.from_bytes(bytes(reflect_byte(b) for b in data), "big")
    return int.from_bytes(data, "big")
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


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    frames_raw = data["frames"]
    segments = data["segments"]

    print("=" * 78)
    print("  SAME-SESSION CRC DEEP ANALYSIS")
    print("=" * 78)

    all_frames = []
    for name, hex_str in frames_raw.items():
        f = parse_frame(hex_str)
        f["name"] = name
        f["hex"] = hex_str
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
        if key in seen:
            print(f"  DUP: {f['name']} == {seen[key]}")
        else:
            seen[key] = f["name"]
            unique.append(f)
    print(f"\n  {len(unique)} unique frames from {len(all_frames)} total\n")

    # Show all unique frames
    print("--- UNIQUE FRAMES ---")
    for f in unique:
        print(f"  {f['name']:30s} inner={f['inner'].hex()}  CRC_LE=0x{f['crc_le']:08X}")

    # Group A1 frames
    a1 = [f for f in unique if f["inner"][4] == 0xA1]
    other = [f for f in unique if f["inner"][4] != 0xA1]

    # Hidden state check: same payload -> same CRC?
    print("\n--- HIDDEN STATE CHECK ---")
    payload_groups = {}
    for f in all_frames:
        payload_groups.setdefault(f["payload"].hex(), []).append(f)
    for key, flist in payload_groups.items():
        if len(flist) > 1:
            crcs = set(f["crc_le"] for f in flist)
            names = [f["name"] for f in flist]
            print(f"  {names}: CRC {'SAME' if len(crcs)==1 else 'DIFFERENT! '+str([hex(c) for c in crcs])}")

    # Pairwise 1-byte diffs for A1 frames
    print(f"\n--- PAIRWISE 1-BYTE DIFFS (A1, {len(a1)} unique) ---")
    xor_map = {}
    for i in range(len(a1)):
        for j in range(i+1, len(a1)):
            fi, fj = a1[i], a1[j]
            pi, pj = fi["payload"], fj["payload"]
            diffs = [k for k in range(16) if pi[k] != pj[k]]
            if len(diffs) == 1:
                pos = diffs[0]
                bxor = pi[pos] ^ pj[pos]
                cxor = fi["crc_le"] ^ fj["crc_le"]
                key = (pos, bxor)
                xor_map.setdefault(key, []).append(cxor)
                print(f"  {fi['name'][:22]:22s} vs {fj['name'][:22]:22s}  "
                      f"byte[{pos:2d}] XOR=0x{bxor:02X}  CRC XOR=0x{cxor:08X}")

    print("\n  Consistency:")
    all_consistent = True
    for (pos, bxor), cxors in sorted(xor_map.items()):
        uniq = set(cxors)
        ok = len(uniq) == 1
        if not ok: all_consistent = False
        print(f"    byte[{pos}] XOR=0x{bxor:02X}: {len(cxors)} pairs -> {'OK' if ok else 'FAIL '+str([hex(c) for c in uniq])}")
    print(f"\n  LINEARITY: {'CONFIRMED' if all_consistent else 'FAILED'}")

    # Per-bit contributions
    print("\n--- PER-BIT CRC CONTRIBUTIONS ---")
    for pos in range(16):
        bit_contribs = {}
        for i in range(len(a1)):
            for j in range(i+1, len(a1)):
                pi, pj = a1[i]["payload"], a1[j]["payload"]
                diffs = [k for k in range(16) if pi[k] != pj[k]]
                if diffs == [pos]:
                    bxor = pi[pos] ^ pj[pos]
                    cxor = a1[i]["crc_le"] ^ a1[j]["crc_le"]
                    if bxor & (bxor - 1) == 0:
                        bit_contribs[bxor.bit_length() - 1] = cxor
        if bit_contribs:
            print(f"\n  Byte[{pos}]:")
            for bit in sorted(bit_contribs):
                print(f"    bit {bit}: 0x{bit_contribs[bit]:08X}")
            if 0 in bit_contribs:
                c0 = bit_contribs[0]
                for b in sorted(bit_contribs):
                    pred = (c0 << b) & 0xFFFFFFFF
                    act = bit_contribs[b]
                    print(f"    bit {b}: predicted=0x{pred:08X} actual=0x{act:08X} {'OK' if pred==act else 'MISMATCH'}")

    # GCD search
    print("\n--- GCD POLYNOMIAL SEARCH ---")
    b10_groups = {}
    for f in a1:
        b10_groups.setdefault(f["payload"][10], []).append(f)

    test_sets = [("All A1", a1)]
    for b10, tfs in b10_groups.items():
        if len(tfs) >= 3:
            test_sets.append((f"b10=0x{b10:02X}", tfs))

    for label, fset in test_sets:
        if len(fset) < 3: continue
        found = False
        for msg_s in range(8):
            for msg_e in range(msg_s + 3, 17):
                for endian in ["le", "be"]:
                    for refl in [False, True]:
                        polys = []
                        for f in fset:
                            msg = f["inner"][msg_s:msg_e]
                            cv = int.from_bytes(f["crc_bytes"], "little" if endian == "le" else "big")
                            mp = bytes_to_poly(msg, reflect=refl)
                            if refl: cv = reflect_bits(cv, 32)
                            polys.append((mp << 32) ^ cv)
                        diffs = [polys[k] ^ polys[0] for k in range(1, len(polys)) if polys[k] != polys[0]]
                        if len(diffs) < 2: continue
                        g = diffs[0]
                        for p in diffs[1:]:
                            g = gf2_gcd(g, p)
                        deg = gf2_degree(g)
                        if 32 <= deg <= 48:
                            found = True
                            pv = g & 0xFFFFFFFF
                            extra = ""
                            if deg == 32:
                                for init in [0x00000000, 0xFFFFFFFF]:
                                    tp = reflect_bits(pv, 32) if refl else pv
                                    table = make_crc_table(tp, reflected=refl)
                                    msg0 = fset[0]["inner"][msg_s:msg_e]
                                    crc0 = int.from_bytes(fset[0]["crc_bytes"], "little" if endian == "le" else "big")
                                    raw0 = compute_crc(msg0, table, init, 0, refl)
                                    xout = raw0 ^ crc0
                                    ok = sum(1 for f in fset
                                             if compute_crc(f["inner"][msg_s:msg_e], table, init, xout, refl)
                                             == int.from_bytes(f["crc_bytes"], "little" if endian == "le" else "big"))
                                    if ok == len(fset):
                                        all_ok = sum(1 for f in unique
                                                     if compute_crc(f["inner"][msg_s:msg_e], table, init, xout, refl)
                                                     == int.from_bytes(f["crc_bytes"], "little" if endian == "le" else "big"))
                                        extra = f" VERIFIED {ok}/{len(fset)} group {all_ok}/{len(unique)} all init=0x{init:08X} xout=0x{xout:08X}"
                            print(f"  [{label}] [{msg_s}:{msg_e}] [{endian}] [{'r' if refl else 'n'}] deg={deg} poly=0x{pv:08X}{extra}")
        if not found:
            print(f"  [{label}] No polynomial found")

    # Well-known CRC brute force (with derived xor_out)
    print("\n--- WELL-KNOWN CRC CHECK ---")
    known = [
        ("CRC-32", 0xEDB88320, True, 0xFFFFFFFF, 0xFFFFFFFF),
        ("CRC-32/BZIP2", 0x04C11DB7, False, 0xFFFFFFFF, 0xFFFFFFFF),
        ("CRC-32C", 0x82F63B78, True, 0xFFFFFFFF, 0xFFFFFFFF),
        ("CRC-32/MPEG-2", 0x04C11DB7, False, 0xFFFFFFFF, 0x00000000),
        ("CRC-32/POSIX", 0x04C11DB7, False, 0x00000000, 0xFFFFFFFF),
        ("CRC-32Q", 0xD5828281, False, 0x00000000, 0x00000000),
        ("CRC-32/JAMCRC", 0xEDB88320, True, 0xFFFFFFFF, 0x00000000),
        ("CRC-32K", 0xEB31D82E, True, 0x00000000, 0x00000000),
        ("CRC-32/AUTOSAR", 0xF4ACFB13, True, 0xFFFFFFFF, 0xFFFFFFFF),
    ]
    for msg_s in range(6):
        for msg_e in range(msg_s + 3, 17):
            for cname, poly, refl, init, xout in known:
                table = make_crc_table(poly, reflected=refl)
                for endian in ["le", "be"]:
                    msg0 = unique[0]["inner"][msg_s:msg_e]
                    raw0 = compute_crc(msg0, table, init, 0, refl)
                    actual0 = int.from_bytes(unique[0]["crc_bytes"], "little" if endian == "le" else "big")
                    dxout = raw0 ^ actual0
                    ok = sum(1 for f in unique
                             if compute_crc(f["inner"][msg_s:msg_e], table, init, dxout, refl)
                             == int.from_bytes(f["crc_bytes"], "little" if endian == "le" else "big"))
                    if ok >= 3:
                        print(f"  [{cname}] [{msg_s}:{msg_e}] [{endian}]: {ok}/{len(unique)} (dxout=0x{dxout:08X})")

    # Setpoint correlation
    print("\n--- SETPOINT CORRELATION ---")
    for seg in segments:
        name = seg["action"]
        bs = seg["broadcast_state"]
        if "temp" in name and name in frames_raw:
            f = parse_frame(frames_raw[name])
            payload = f["inner"][:16]
            print(f"  {name:30s} byte[14]=0x{payload[14]:02X}={payload[14]:3d}  "
                  f"setpoint_before={bs['setpoint_f']}F  "
                  f"b10=0x{payload[10]:02X} b11=0x{payload[11]:02X}")

    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()

