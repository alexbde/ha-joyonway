#!/usr/bin/env python3
"""
Deep analysis of same-session CRC captures.
Parses all frames, groups by type, checks linearity, attempts GCD per group.
"""
import json, os, sys

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

def parse_frame(hex_str: str) -> dict:
    raw = bytes.fromhex(hex_str)
    inner = unescape(raw[1:-1])  # unescape everything between 0x1A and 0x1D
    full = bytes([raw[0]]) + inner + bytes([raw[-1]])
    return {
        "raw_hex": hex_str,
        "raw": raw,
        "raw_len": len(raw),
        "full": full,  # unescaped with start/end
        "full_len": len(full),
        "inner": inner,  # unescaped without start/end
    }

# --- GF(2) polynomial arithmetic ---
def gf2_degree(a): return a.bit_length() - 1 if a else -1
def gf2_mod(a, b):
    db = gf2_degree(b)
    if db < 0: raise ZeroDivisionError
    while True:
        da = gf2_degree(a)
        if da < db: return a
        a ^= b << (da - db)
def gf2_gcd(a, b):
    while b:
        a, b = b, gf2_mod(a, b)
    return a
def reflect_bits(value, width):
    r = 0
    for i in range(width):
        if value & (1 << i):
            r |= 1 << (width - 1 - i)
    return r
def reflect_byte(b):
    return reflect_bits(b, 8)
def bytes_to_poly(data, reflect=False):
    if reflect:
        return int.from_bytes(bytes(reflect_byte(b) for b in data), "big")
    return int.from_bytes(data, "big")

def make_crc_table(poly, reflected):
    table = []
    if reflected:
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
            table.append(crc)
    else:
        for i in range(256):
            crc = i << 24
            for _ in range(8):
                crc = ((crc << 1) & 0xFFFFFFFF) ^ poly if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
            table.append(crc)
    return table

def compute_crc(data, table, init, xor_out, reflected):
    crc = init
    if reflected:
        for byte in data:
            crc = (crc >> 8) ^ table[(crc ^ byte) & 0xFF]
    else:
        for byte in data:
            crc = ((crc << 8) & 0xFFFFFFFF) ^ table[((crc >> 24) ^ byte) & 0xFF]
    return crc ^ xor_out


def try_gcd(frames_parsed, msg_start, msg_end, crc_start, crc_len, crc_endian, reflect_input):
    """Try GCD extraction with explicit CRC position."""
    polys = []
    for f in frames_parsed:
        data = f["full"]
        if msg_end > len(data) or crc_start + crc_len > len(data):
            return None
        msg_bytes = data[msg_start:msg_end]
        crc_bytes = data[crc_start:crc_start + crc_len]
        msg_poly = bytes_to_poly(msg_bytes, reflect=reflect_input)
        crc_val = int.from_bytes(crc_bytes, "little" if crc_endian == "le" else "big")
        if reflect_input:
            crc_val = reflect_bits(crc_val, 32)
        combined = (msg_poly << 32) ^ crc_val
        polys.append(combined)

    diffs = [polys[i] ^ polys[0] for i in range(1, len(polys)) if polys[i] != polys[0]]
    if len(diffs) < 2:
        return None

    result = diffs[0]
    for p in diffs[1:]:
        result = gf2_gcd(result, p)
    return result


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_crc", "crc_session.json")) as f:
        data = json.load(f)

    frames_raw = data["frames"]
    segments = data["segments"]

    print("=" * 78)
    print("  SAME-SESSION CRC ANALYSIS — 24 frames")
    print("=" * 78)

    # ─── 1. Parse all frames and show structure ───
    print("\n┌─── 1. FRAME STRUCTURE ───")
    all_frames = []
    for name, hex_str in frames_raw.items():
        f = parse_frame(hex_str)
        f["name"] = name
        all_frames.append(f)

    # Group by frame type (byte[4] after unescape)
    groups = {}
    for f in all_frames:
        frame_type = f["full"][4] if len(f["full"]) > 4 else 0xFF
        groups.setdefault(frame_type, []).append(f)

    for ftype, frames in sorted(groups.items()):
        print(f"\n  Frame type 0x{ftype:02X} — {len(frames)} frames:")
        for f in frames:
            inner = f["inner"]
            print(f"    {f['name']:30s} raw={f['raw_len']}b unesc={f['full_len']}b  "
                  f"inner_hex={inner.hex()}")

    # ─── 2. Detailed byte comparison for 0xA1 button commands ───
    print("\n┌─── 2. BYTE-BY-BYTE COMPARISON (0xA1 button commands) ───")
    a1_frames = groups.get(0xA1, [])
    if a1_frames:
        # Find common length
        lengths = set(f["full_len"] for f in a1_frames)
        print(f"  Lengths: {lengths}")
        # Show each byte position
        max_len = max(f["full_len"] for f in a1_frames)
        print(f"\n  {'Byte':>4s} | " + " | ".join(f"{f['name'][:12]:>12s}" for f in a1_frames[:8]))
        print(f"  {'':>4s}-+-" + "-+-".join("-" * 12 for _ in a1_frames[:8]))
        for pos in range(max_len):
            vals = []
            for f in a1_frames[:8]:
                if pos < f["full_len"]:
                    vals.append(f"  0x{f['full'][pos]:02X}")
                else:
                    vals.append("    --")
            # Mark varying positions
            unique = set(v.strip() for v in vals if v.strip() != "--")
            marker = " *" if len(unique) > 1 else "  "
            print(f"  {pos:4d} |{marker}" + " | ".join(f"{v:>10s}" for v in vals))

    # ─── 3. Extract CRC bytes and analyze ───
    print("\n┌─── 3. CRC EXTRACTION ───")
    print("  Assuming CRC is the last 4 bytes before 0x1D delimiter")
    print("  (i.e., last 4 bytes of unescaped inner payload)")

    for f in all_frames:
        inner = f["inner"]
        payload = inner[:-4]
        crc_bytes = inner[-4:]
        crc_le = int.from_bytes(crc_bytes, "little")
        crc_be = int.from_bytes(crc_bytes, "big")
        f["payload"] = payload
        f["crc_le"] = crc_le
        f["crc_be"] = crc_be
        f["crc_bytes"] = crc_bytes
        print(f"  {f['name']:30s} payload[{len(payload)}]={payload.hex()[:40]:40s}  "
              f"CRC_BE={crc_be:08X}  CRC_LE={crc_le:08X}")

    # ─── 4. Linearity check within 0xA1 group ───
    print("\n┌─── 4. LINEARITY CHECK (0xA1 frames) ───")
    # For pairs that differ in exactly ONE byte position, check if CRC XOR is consistent
    for i in range(len(a1_frames)):
        for j in range(i+1, len(a1_frames)):
            fi, fj = a1_frames[i], a1_frames[j]
            pi, pj = fi["payload"], fj["payload"]
            if len(pi) != len(pj):
                continue
            diff_positions = [k for k in range(len(pi)) if pi[k] != pj[k]]
            if len(diff_positions) == 1:
                pos = diff_positions[0]
                byte_xor = pi[pos] ^ pj[pos]
                crc_xor_le = fi["crc_le"] ^ fj["crc_le"]
                crc_xor_be = fi["crc_be"] ^ fj["crc_be"]
                print(f"  {fi['name'][:20]:20s} vs {fj['name'][:20]:20s}  "
                      f"byte[{pos}] XOR=0x{byte_xor:02X}  CRC_LE XOR=0x{crc_xor_le:08X}")
            elif len(diff_positions) == 2:
                p1, p2 = diff_positions
                print(f"  {fi['name'][:20]:20s} vs {fj['name'][:20]:20s}  "
                      f"differ at byte[{p1}]+byte[{p2}]  "
                      f"CRC_LE XOR=0x{fi['crc_le'] ^ fj['crc_le']:08X}")

    # ─── 5. Group by identical payload structure (same varying bytes) ───
    print("\n┌─── 5. TEMP-ONLY SUBGROUP ANALYSIS ───")
    temp_frames = [f for f in a1_frames if "temp" in f["name"]]
    print(f"  {len(temp_frames)} temperature frames")

    # Sub-group by byte[11] (0x80 vs 0x99 vs 0x98)
    temp_groups = {}
    for f in temp_frames:
        key = f["payload"][10] if len(f["payload"]) > 10 else 0
        temp_groups.setdefault(key, []).append(f)

    for b11, tframes in sorted(temp_groups.items()):
        print(f"\n  byte[11]=0x{b11:02X} group ({len(tframes)} frames):")
        for f in tframes:
            print(f"    {f['name']:30s} payload={f['payload'].hex()}  CRC_LE={f['crc_le']:08X}")

        if len(tframes) >= 2:
            # Check linearity: all should differ only at setpoint byte
            ref = tframes[0]
            for other in tframes[1:]:
                diffs = [k for k in range(len(ref["payload"])) if ref["payload"][k] != other["payload"][k]]
                if diffs:
                    print(f"    → diff vs ref at byte(s) {diffs}: "
                          f"{[f'0x{ref[\"payload\"][d]:02X}→0x{other[\"payload\"][d]:02X}' for d in diffs]}")
            
            # GCD for this subgroup
            print(f"\n    GCD analysis (byte[11]=0x{b11:02X} subgroup):")
            for msg_start, msg_end, crc_start in [(1, -5, -5), (0, -5, -5)]:
                # Adjust negative indices
                for f in tframes:
                    plen = len(f["full"])
                actual_end = plen + msg_end if msg_end < 0 else msg_end
                actual_crc = plen + crc_start if crc_start < 0 else crc_start

                for endian in ["le", "be"]:
                    for refl in [False, True]:
                        g = try_gcd(tframes, msg_start, actual_end, actual_crc, 4, endian, refl)
                        deg = gf2_degree(g) if g else -1
                        if deg >= 32:
                            print(f"      [{msg_start}:{actual_end}] crc@{actual_crc} "
                                  f"[{endian}] [{'ref' if refl else 'norm'}]: "
                                  f"degree={deg} poly=0x{g & 0xFFFFFFFF:08X} ★")

    # ─── 6. Systematic CRC position search ───
    print("\n┌─── 6. SYSTEMATIC CRC POSITION + MSG RANGE SEARCH (0xA1 group) ───")
    # All 0xA1 frames should be 22 bytes unescaped (with start/end)
    # Try different message ranges and CRC positions

    # Only use frames with same length
    common_len = max(set(f["full_len"] for f in a1_frames), key=lambda l: sum(1 for f in a1_frames if f["full_len"] == l))
    same_len_frames = [f for f in a1_frames if f["full_len"] == common_len]
    print(f"  Using {len(same_len_frames)} frames of length {common_len}")

    hits = []
    for crc_start in range(common_len - 4):
        for msg_start in range(crc_start):
            for msg_end in range(msg_start + 2, crc_start + 1):
                for endian in ["le", "be"]:
                    for refl in [False, True]:
                        g = try_gcd(same_len_frames, msg_start, msg_end, crc_start, 4, endian, refl)
                        deg = gf2_degree(g) if g else -1
                        if deg == 32:
                            hits.append({
                                "msg": f"[{msg_start}:{msg_end}]",
                                "crc": f"@{crc_start}",
                                "endian": endian,
                                "refl": refl,
                                "poly": g & 0xFFFFFFFF,
                            })
                        elif deg > 32 and deg <= 40:
                            hits.append({
                                "msg": f"[{msg_start}:{msg_end}]",
                                "crc": f"@{crc_start}",
                                "endian": endian,
                                "refl": refl,
                                "poly": g,
                                "degree": deg,
                            })

    if hits:
        print(f"\n  Found {len(hits)} promising configurations:")
        for h in hits[:20]:
            deg = h.get("degree", 32)
            print(f"    msg{h['msg']} crc{h['crc']} [{h['endian']}] "
                  f"[{'ref' if h['refl'] else 'norm'}] deg={deg} "
                  f"poly=0x{h['poly']:08X}")
    else:
        print("  No degree-32 GCD found in any configuration.")
        print("  Trying with subgroups (temp-only same byte[11])...")

        for b11, tframes in sorted(temp_groups.items()):
            if len(tframes) < 3:
                continue
            same_len = [f for f in tframes if f["full_len"] == common_len]
            if len(same_len) < 3:
                continue
            print(f"\n  Sub-group byte[11]=0x{b11:02X} ({len(same_len)} frames):")
            for crc_start in range(common_len - 4):
                for msg_start in range(crc_start):
                    for msg_end in range(msg_start + 2, crc_start + 1):
                        for endian in ["le", "be"]:
                            for refl in [False, True]:
                                g = try_gcd(same_len, msg_start, msg_end, crc_start, 4, endian, refl)
                                deg = gf2_degree(g) if g else -1
                                if deg >= 32:
                                    print(f"    msg[{msg_start}:{msg_end}] crc@{crc_start} "
                                          f"[{endian}] [{'ref' if refl else 'norm'}] "
                                          f"deg={deg} poly=0x{g & 0xFFFFFFFF:08X} ★")

    # ─── 7. Check for non-standard structures ───
    print("\n┌─── 7. RAW (ESCAPED) WIRE ANALYSIS ───")
    print("  Trying GCD on raw wire bytes (before unescaping)")

    # Use raw wire bytes (no start/end delimiters)
    wire_frames = []
    for f in same_len_frames:
        raw = f["raw"]
        wire = raw[1:-1]  # strip 0x1A and 0x1D
        wire_frames.append({"name": f["name"], "wire": wire, "wire_len": len(wire)})

    wire_lengths = set(wf["wire_len"] for wf in wire_frames)
    print(f"  Wire lengths: {wire_lengths}")

    # For same-length wire frames, try GCD
    for wlen in wire_lengths:
        wfs = [wf for wf in wire_frames if wf["wire_len"] == wlen]
        if len(wfs) < 3:
            continue
        print(f"\n  Wire length {wlen} ({len(wfs)} frames):")
        for crc_start in range(wlen - 4):
            for msg_start in range(crc_start):
                for msg_end in range(msg_start + 2, crc_start + 1):
                    for endian in ["le", "be"]:
                        for refl in [False, True]:
                            polys = []
                            for wf in wfs:
                                w = wf["wire"]
                                msg_bytes = w[msg_start:msg_end]
                                crc_bytes = w[crc_start:crc_start + 4]
                                msg_poly = bytes_to_poly(msg_bytes, reflect=refl)
                                crc_val = int.from_bytes(crc_bytes, "little" if endian == "le" else "big")
                                if refl: crc_val = reflect_bits(crc_val, 32)
                                polys.append((msg_poly << 32) ^ crc_val)
                            diffs = [polys[i] ^ polys[0] for i in range(1, len(polys)) if polys[i] != polys[0]]
                            if len(diffs) < 2: continue
                            g = diffs[0]
                            for p in diffs[1:]:
                                g = gf2_gcd(g, p)
                            deg = gf2_degree(g)
                            if deg == 32:
                                print(f"    msg[{msg_start}:{msg_end}] crc@{crc_start} "
                                      f"[{endian}] [{'ref' if refl else 'norm'}] "
                                      f"deg={deg} poly=0x{g & 0xFFFFFFFF:08X} ★★★")

    # ─── 8. Direct CRC constant analysis ───
    print("\n┌─── 8. DIRECT XOR ANALYSIS (pairs differing in 1 byte) ───")
    # For each pair of frames differing in exactly 1 payload byte,
    # the CRC XOR depends ONLY on the byte XOR and position (for any linear CRC).
    # Group by (position, byte_xor) and check consistency.
    xor_map = {}
    for i in range(len(same_len_frames)):
        for j in range(i+1, len(same_len_frames)):
            fi, fj = same_len_frames[i], same_len_frames[j]
            pi, pj = fi["payload"], fj["payload"]
            if len(pi) != len(pj): continue
            diffs = [k for k in range(len(pi)) if pi[k] != pj[k]]
            if len(diffs) == 1:
                pos = diffs[0]
                bxor = pi[pos] ^ pj[pos]
                cxor = fi["crc_le"] ^ fj["crc_le"]
                key = (pos, bxor)
                xor_map.setdefault(key, []).append((fi["name"], fj["name"], cxor))

    print(f"  Found {len(xor_map)} unique (position, byte_xor) pairs:")
    consistent = 0
    inconsistent = 0
    for (pos, bxor), entries in sorted(xor_map.items()):
        crc_xors = set(cxor for _, _, cxor in entries)
        status = "✅" if len(crc_xors) == 1 else "❌ INCONSISTENT"
        if len(crc_xors) == 1:
            consistent += 1
        else:
            inconsistent += 1
        print(f"    byte[{pos}] XOR=0x{bxor:02X}: CRC XOR={', '.join(f'0x{c:08X}' for c in crc_xors)} "
              f"({len(entries)} pairs) {status}")

    print(f"\n  Consistent: {consistent}, Inconsistent: {inconsistent}")
    if inconsistent > 0:
        print("  ⚠ INCONSISTENT XOR means there's a hidden varying input to the CRC!")
        print("  This could be a counter, timestamp, or state-dependent seed.")

        # Check: is there a hidden counter or sequence number?
        print("\n  Checking for hidden state in identical-payload frames:")
        seen_payloads = {}
        for f in all_frames:
            key = f["payload"].hex()
            seen_payloads.setdefault(key, []).append(f)
        for key, flist in seen_payloads.items():
            if len(flist) > 1:
                print(f"    Same payload {key[:40]}... appears {len(flist)} times:")
                for f in flist:
                    print(f"      {f['name']:30s} CRC_LE={f['crc_le']:08X}")
                crcs = [f["crc_le"] for f in flist]
                if len(set(crcs)) == 1:
                    print(f"      → Same CRC ✅ (no hidden state for same payload)")
                else:
                    print(f"      → Different CRCs ❌ (hidden state exists!)")

    print("\n" + "=" * 78)
    print("  ANALYSIS COMPLETE")
    print("=" * 78)


if __name__ == "__main__":
    main()

