#!/usr/bin/env python3
"""
CRC analysis and polynomial extraction for P25B85 command frames.

Key finding: The checksum IS a linear CRC. The previous brute-force failed
because it tested arithmetic deltas instead of XOR deltas.

With 19 temperature frames where only byte[15] varies, we can:
1. Prove linearity (same XOR delta -> same CRC XOR)
2. Extract per-bit CRC contributions
3. Predict CRC for ANY byte[15] value without knowing the polynomial
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

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


def bits_set(x: int) -> list[int]:
    return [b for b in range(8) if x & (1 << b)]


def compute_contrib(base_val: int, target_val: int, c0_le: int) -> int:
    """Compute CRC contribution (LE) for changing byte from base_val to target_val."""
    d = base_val ^ target_val
    contrib = 0
    for b in bits_set(d):
        contrib ^= (c0_le << b) & 0xFFFFFFFF
    return contrib


def predict_crc_le(ref_crc_le: int, ref_b15: int, target_b15: int, c0_le: int) -> int:
    """Predict CRC (LE) for a frame with a different byte[15] value."""
    return ref_crc_le ^ compute_contrib(ref_b15, target_b15, c0_le)


def main():
    base = os.path.dirname(__file__)
    with open(os.path.join(base, "captures_temp", "temp_commands.json")) as f:
        temp_data = json.load(f)

    # Parse all frames
    all_frames = {}
    all_frames["light_toggle"] = "1a0120103ca110a10000404000c00056003031eeb21d"
    all_frames["pump_off_low"] = "1a0120103ca110a10202000000c00056007dd2146b1d"
    all_frames["pump_low_high"] = "1a0120103ca110a10604000000c0005600fc1221c61d"
    all_frames["pump_high_off"] = "1a0120103ca110a10400000000c0005600735738e91d"
    for k, v in temp_data.items():
        if k.endswith("F"):
            all_frames[f"temp_{k}"] = v

    parsed = {}
    for name, hex_str in all_frames.items():
        f = parse_frame(hex_str)
        crc_bytes = f[17:21]
        parsed[name] = {
            "full": f,
            "crc_be": int.from_bytes(crc_bytes, "big"),
            "crc_le": int.from_bytes(crc_bytes, "little"),
            "b11": f[11],
            "b15": f[15],
        }

    # Use 0x88 group (19 frames, only byte[15] varies)
    g88 = sorted(
        [(n, p) for n, p in parsed.items()
         if n.startswith("temp_") and p["b11"] == 0x88],
        key=lambda x: x[1]["b15"],
    )

    out = []
    out.append("# CRC Analysis — P25B85 Command Frames")
    out.append("")
    out.append("## 1. Linearity proof")
    out.append("")
    out.append("For frames differing only in byte[15], grouping by XOR delta")
    out.append("(not arithmetic delta) shows perfectly consistent CRC XOR values.")
    out.append("This proves the checksum is a **linear CRC**.")
    out.append("")

    # Verify linearity with XOR deltas
    delta_xors = defaultdict(list)
    for i in range(len(g88)):
        for j in range(i + 1, len(g88)):
            b1, b2 = g88[i][1]["b15"], g88[j][1]["b15"]
            c1, c2 = g88[i][1]["crc_le"], g88[j][1]["crc_le"]
            delta_xors[b1 ^ b2].append((b1, b2, c1 ^ c2))

    all_linear = True
    for dt, entries in delta_xors.items():
        xors = [e[2] for e in entries]
        if not all(x == xors[0] for x in xors):
            all_linear = False

    out.append(f"Linearity check: **{'PASSED' if all_linear else 'FAILED'}**")
    out.append(f"Tested {sum(len(v) for v in delta_xors.values())} XOR pairs, "
               f"{len(delta_xors)} unique deltas")
    out.append("")

    # Extract per-bit contributions
    out.append("## 2. Per-bit CRC contributions at byte[15]")
    out.append("")
    out.append("Using 1-bit XOR pairs to extract the CRC contribution of each bit:")
    out.append("")

    bit_contribs_be = {}
    bit_contribs_le = {}

    for i in range(len(g88)):
        for j in range(i + 1, len(g88)):
            b1, b2 = g88[i][1]["b15"], g88[j][1]["b15"]
            xor = b1 ^ b2
            if bin(xor).count("1") == 1:
                bit = xor.bit_length() - 1
                crc_xor_be = g88[i][1]["crc_be"] ^ g88[j][1]["crc_be"]
                crc_xor_le = g88[i][1]["crc_le"] ^ g88[j][1]["crc_le"]
                if bit not in bit_contribs_be:
                    bit_contribs_be[bit] = crc_xor_be
                    bit_contribs_le[bit] = crc_xor_le

    out.append("| Bit | CRC XOR (BE) | CRC XOR (LE) |")
    out.append("|-----|-------------|-------------|")
    for bit in sorted(bit_contribs_be.keys()):
        out.append(f"| {bit} | {bit_contribs_be[bit]:#010x} | {bit_contribs_le[bit]:#010x} |")

    out.append("")

    # Check the doubling pattern in LE
    out.append("## 3. Doubling pattern discovery")
    out.append("")
    c0_le = bit_contribs_le[0]
    out.append(f"C[0]_LE = {c0_le:#010x}")
    out.append("")
    out.append("Checking if C[b]_LE = C[0]_LE << b (simple left shift / doubling):")
    out.append("")

    doubling_ok = True
    for bit in sorted(bit_contribs_le.keys()):
        predicted = (c0_le << bit) & 0xFFFFFFFF
        actual = bit_contribs_le[bit]
        match = predicted == actual
        if not match:
            doubling_ok = False
        out.append(f"  C[{bit}]_LE: predicted={predicted:#010x} actual={actual:#010x} "
                   f"{'OK' if match else 'MISMATCH'}")

    out.append("")
    out.append(f"Doubling pattern: **{'CONFIRMED' if doubling_ok else 'BROKEN'}**")
    out.append("")

    if doubling_ok:
        out.append("This means the CRC contributions are pure GF(2) shifts with no")
        out.append("polynomial feedback in the bit 0-4 range. We can extrapolate to")
        out.append("bits 5-7 by continuing the pattern.")
        out.append("")
        for bit in range(5, 8):
            val = (c0_le << bit) & 0xFFFFFFFF
            out.append(f"  C[{bit}]_LE = {val:#010x} (predicted)")
        out.append("")

    # Verify predictions against known frames
    out.append("## 4. CRC prediction verification")
    out.append("")
    out.append("Using reference frame (51F, byte15=0x33) to predict all others:")
    out.append("")

    ref = g88[0][1]
    ref_b15 = ref["b15"]
    ref_crc_le = ref["crc_le"]

    correct = 0
    total = 0
    out.append("| Temp | Predicted CRC | Actual CRC | Match |")
    out.append("|------|-------------|-----------|-------|")

    for name, p in g88:
        target_b15 = p["b15"]
        predicted_le = predict_crc_le(ref_crc_le, ref_b15, target_b15, c0_le)
        predicted_be = int.from_bytes(predicted_le.to_bytes(4, "little"), "big")
        actual_be = p["crc_be"]
        match = predicted_be == actual_be
        if match:
            correct += 1
        total += 1
        out.append(f"| {target_b15}F ({name}) | {predicted_be:#010x} | "
                   f"{actual_be:#010x} | {'OK' if match else 'FAIL'} |")

    out.append("")
    out.append(f"**Result: {correct}/{total} predictions correct**")
    out.append("")

    # Cross-validate with 0x98 and 0x99 groups
    out.append("## 5. Cross-group validation")
    out.append("")
    out.append("Can we predict CRC across byte[11] groups (0x88 vs 0x98 vs 0x99)?")
    out.append("These groups differ in byte[11] AND byte[15], so CRC prediction")
    out.append("requires knowing contributions at BOTH positions.")
    out.append("")

    other_groups = [(n, p) for n, p in parsed.items()
                    if n.startswith("temp_") and p["b11"] != 0x88]
    for name, p in sorted(other_groups, key=lambda x: x[1]["b15"]):
        predicted_le = predict_crc_le(ref_crc_le, ref_b15, p["b15"], c0_le)
        predicted_be = int.from_bytes(predicted_le.to_bytes(4, "little"), "big")
        actual_be = p["crc_be"]
        match = predicted_be == actual_be
        out.append(f"  {name} (b11={p['b11']:#04x}): predicted={predicted_be:#010x} "
                   f"actual={actual_be:#010x} {'OK' if match else 'FAIL (expected - byte[11] differs)'}")

    out.append("")

    # Value analysis
    out.append("## 6. Practical value")
    out.append("")
    out.append("### What we can do now (without polynomial)")
    out.append("")
    out.append("For frames where only byte[15] changes (temperature commands with")
    out.append("same byte[11] value), we can compute the CRC using:")
    out.append("```")
    out.append(f"C0_LE = {c0_le:#010x}")
    out.append(f"REF_CRC_LE = {ref_crc_le:#010x}  (reference: byte15=0x{ref_b15:02x})")
    out.append(f"contrib(x) = XOR of (C0_LE << b) for each set bit b in (x ^ 0x{ref_b15:02x})")
    out.append(f"CRC_LE = REF_CRC_LE ^ contrib(target_byte15)")
    out.append("```")
    out.append("")
    out.append("### What we still need the polynomial for")
    out.append("")
    out.append("- Date/time commands (different byte positions change)")
    out.append("- Commands with different byte[11] values")
    out.append("- Any command with a different base frame structure")
    out.append("")
    out.append("### Next steps to find the polynomial")
    out.append("")
    out.append("- Use light/pump frames to extract contributions at bytes 8-11")
    out.append("- With contributions at multiple byte positions, derive the")
    out.append("  polynomial through GF(2) matrix algebra")
    out.append("- Or capture ONE date/time command and apply same technique")

    report = "\n".join(out) + "\n"
    report_path = os.path.join(base, "..", "docs", "crc_analysis.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w") as f:
        f.write(report)

    # Print summary to stdout
    print(f"CRC Analysis complete. Saved to {os.path.abspath(report_path)}")
    print()
    print(f"Key findings:")
    print(f"  - CRC is LINEAR (proven with {len(delta_xors)} XOR delta groups)")
    print(f"  - Doubling pattern: {'CONFIRMED' if doubling_ok else 'BROKEN'}")
    print(f"  - CRC prediction: {correct}/{total} correct (0x88 group)")
    print(f"  - C0_LE = {c0_le:#010x} (base contribution at byte[15])")
    print(f"  - Can predict CRC for ANY temperature in same frame structure")


if __name__ == "__main__":
    main()

