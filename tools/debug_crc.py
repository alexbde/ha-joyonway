#!/usr/bin/env python3
"""Debug: try reversed byte order and other message representations."""
import json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from crack_crc import (parse_frame, gf2_degree, gf2_gcd, reflect_bits,
                        make_crc_table, compute_crc)

base = os.path.dirname(__file__)
with open(os.path.join(base, 'captures_temp', 'temp_commands.json')) as f:
    temp_data = json.load(f)

all_hex = {}
all_hex["light_toggle"] = "1a0120103ca110a10000404000c00056003031eeb21d"
all_hex["pump_off_low"] = "1a0120103ca110a10202000000c00056007dd2146b1d"
all_hex["pump_low_high"] = "1a0120103ca110a10604000000c0005600fc1221c61d"
all_hex["pump_high_off"] = "1a0120103ca110a10400000000c0005600735738e91d"
for k, v in temp_data.items():
    if k.endswith('F'):
        all_hex[f"temp_{k}"] = v

all_frames = []
for name, hex_str in all_hex.items():
    f = parse_frame(hex_str)
    all_frames.append({'name': name, 'full': f})

print(f'{len(all_frames)} frames')

configs = [
    # (start, end, msg_order, crc_endian, description)
    (1, 17, 'big',    'little', 'bytes[1:17] big-endian msg, LE crc'),
    (1, 17, 'little', 'little', 'bytes[1:17] little-endian msg, LE crc'),
    (1, 17, 'big',    'big',    'bytes[1:17] big-endian msg, BE crc'),
    (1, 17, 'little', 'big',    'bytes[1:17] little-endian msg, BE crc'),
    (0, 17, 'big',    'little', 'bytes[0:17] big-endian msg, LE crc'),
    (0, 17, 'little', 'little', 'bytes[0:17] little-endian msg, LE crc'),
    (8, 17, 'big',    'little', 'bytes[8:17] big-endian msg, LE crc'),
    (8, 17, 'little', 'little', 'bytes[8:17] little-endian msg, LE crc'),
    (1, 16, 'big',    'little', 'bytes[1:16] big-endian msg, LE crc'),
    (1, 16, 'little', 'little', 'bytes[1:16] little-endian msg, LE crc'),
]

for start, end, msg_order, crc_endian, desc in configs:
    polys = []
    for f in all_frames:
        msg = f['full'][start:end]
        crc = f['full'][17:21]
        msg_poly = int.from_bytes(msg, msg_order)
        crc_val = int.from_bytes(crc, crc_endian)
        combined = (msg_poly << 32) ^ crc_val
        polys.append(combined)

    diffs = [polys[i] ^ polys[0] for i in range(1, len(polys)) if polys[i] != polys[0]]
    if not diffs:
        continue
    g = diffs[0]
    for d in diffs[1:]:
        g = gf2_gcd(g, d)
    deg = gf2_degree(g)
    if deg >= 32:
        print(f'  {desc}: GCD degree={deg} value={g:#x}')
        if deg == 32:
            poly_val = g & 0xFFFFFFFF
            poly_ref = reflect_bits(poly_val, 32)
            print(f'    *** POLYNOMIAL: normal={poly_val:#010x} reflected={poly_ref:#010x} ***')
