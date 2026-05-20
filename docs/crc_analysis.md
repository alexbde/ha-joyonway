# CRC Analysis — P25B85 Command Frames

## 1. Linearity proof

For frames differing only in byte[15], grouping by XOR delta
(not arithmetic delta) shows perfectly consistent CRC XOR values.
This proves the checksum is a **linear CRC**.

Linearity check: **PASSED**
Tested 171 XOR pairs, 60 unique deltas

## 2. Per-bit CRC contributions at byte[15]

Using 1-bit XOR pairs to extract the CRC contribution of each bit:

| Bit | CRC XOR (BE) | CRC XOR (LE) |
|-----|-------------|-------------|
| 0 | 0x87acd801 | 0x01d8ac87 |
| 1 | 0x0e59b103 | 0x03b1590e |
| 2 | 0x1cb26207 | 0x0762b21c |
| 3 | 0x3864c50e | 0x0ec56438 |
| 4 | 0x70c88a1d | 0x1d8ac870 |

## 3. Doubling pattern discovery

C[0]_LE = 0x01d8ac87

Checking if C[b]_LE = C[0]_LE << b (simple left shift / doubling):

  C[0]_LE: predicted=0x01d8ac87 actual=0x01d8ac87 OK
  C[1]_LE: predicted=0x03b1590e actual=0x03b1590e OK
  C[2]_LE: predicted=0x0762b21c actual=0x0762b21c OK
  C[3]_LE: predicted=0x0ec56438 actual=0x0ec56438 OK
  C[4]_LE: predicted=0x1d8ac870 actual=0x1d8ac870 OK

Doubling pattern: **CONFIRMED**

This means the CRC contributions are pure GF(2) shifts with no
polynomial feedback in the bit 0-4 range. We can extrapolate to
bits 5-7 by continuing the pattern.

  C[5]_LE = 0x3b1590e0 (predicted)
  C[6]_LE = 0x762b21c0 (predicted)
  C[7]_LE = 0xec564380 (predicted)

## 4. CRC prediction verification

Using reference frame (51F, byte15=0x33) to predict all others:

| Temp | Predicted CRC | Actual CRC | Match |
|------|-------------|-----------|-------|
| 51F (temp_51F) | 0x80db4546 | 0x80db4546 | OK |
| 53F (temp_53F) | 0x92309642 | 0x92309642 | OK |
| 55F (temp_55F) | 0x9c692741 | 0x9c692741 | OK |
| 57F (temp_57F) | 0xb6e6314b | 0xb6e6314b | OK |
| 59F (temp_59F) | 0xb8bf8048 | 0xb8bf8048 | OK |
| 60F (temp_60F) | 0x2df88b4d | 0x2df88b4d | OK |
| 62F (temp_62F) | 0x23a13a4e | 0x23a13a4e | OK |
| 64F (temp_64F) | 0x59579814 | 0x59579814 | OK |
| 66F (temp_66F) | 0x570e2917 | 0x570e2917 | OK |
| 68F (temp_68F) | 0x45e5fa13 | 0x45e5fa13 | OK |
| 69F (temp_69F) | 0xc2492212 | 0xc2492212 | OK |
| 71F (temp_71F) | 0xcc109311 | 0xcc109311 | OK |
| 73F (temp_73F) | 0xe69f851b | 0xe69f851b | OK |
| 75F (temp_75F) | 0xe8c63418 | 0xe8c63418 | OK |
| 80F (temp_80F) | 0x299f1209 | 0x299f1209 | OK |
| 82F (temp_82F) | 0x27c6a30a | 0x27c6a30a | OK |
| 84F (temp_84F) | 0x352d700e | 0x352d700e | OK |
| 86F (temp_86F) | 0x3b74c10d | 0x3b74c10d | OK |
| 87F (temp_87F) | 0xbcd8190c | 0xbcd8190c | OK |

**Result: 19/19 predictions correct**

## 5. Cross-group validation

Can we predict CRC across byte[11] groups (0x88 vs 0x98 vs 0x99)?
These groups differ in byte[11] AND byte[15], so CRC prediction
requires knowing contributions at BOTH positions.

  temp_50F (b11=0x98): predicted=0x07779d47 actual=0xcb80efa1 FAIL (expected - byte[11] differs)
  temp_77F (b11=0x98): predicted=0xfa2de71c actual=0x36da95fa FAIL (expected - byte[11] differs)
  temp_78F (b11=0x98): predicted=0x73d88e1e actual=0xbf2ffcf8 FAIL (expected - byte[11] differs)
  temp_89F (b11=0x99): predicted=0x96570f06 actual=0x4bc82aaf FAIL (expected - byte[11] differs)
  temp_91F (b11=0x99): predicted=0x980ebe05 actual=0x45919bac FAIL (expected - byte[11] differs)
  temp_93F (b11=0x99): predicted=0x8ae56d01 actual=0x577a48a8 FAIL (expected - byte[11] differs)
  temp_95F (b11=0x99): predicted=0x84bcdc02 actual=0x5923f9ab FAIL (expected - byte[11] differs)
  temp_96F (b11=0x99): predicted=0xb9c78d2f actual=0x6458a886 FAIL (expected - byte[11] differs)
  temp_98F (b11=0x99): predicted=0xb79e3c2c actual=0x6a011985 FAIL (expected - byte[11] differs)
  temp_100F (b11=0x99): predicted=0xa575ef28 actual=0x78eaca81 FAIL (expected - byte[11] differs)
  temp_102F (b11=0x99): predicted=0xab2c5e2b actual=0x76b37b82 FAIL (expected - byte[11] differs)
  temp_104F (b11=0x99): predicted=0x81a34821 actual=0x5c3c6d88 FAIL (expected - byte[11] differs)

## 6. Practical value

### What we can do now (without polynomial)

For frames where only byte[15] changes (temperature commands with
same byte[11] value), we can compute the CRC using:
```
C0_LE = 0x01d8ac87
REF_CRC_LE = 0x4645db80  (reference: byte15=0x33)
contrib(x) = XOR of (C0_LE << b) for each set bit b in (x ^ 0x33)
CRC_LE = REF_CRC_LE ^ contrib(target_byte15)
```

### What we still need the polynomial for

- Date/time commands (different byte positions change)
- Commands with different byte[11] values
- Any command with a different base frame structure

### Next steps to find the polynomial

Attempted: GF(2) polynomial GCD (the standard CRC reverse-engineering
algorithm). With temp-only frames (0x88 group): GCD = x^40 + 0x01d8ac87
(degree 40, exactly our C[0]_LE as expected). With ALL frames (temp +
light + pump): GCD = 1 (coprime).

This means the light/pump frames (captured in a separate session) don't
share the same polynomial relationship as the temperature frames. Possible
explanations:

1. **Session-dependent state**: the CRC includes a counter, timestamp, or
   other value that changes between capture sessions
2. **Non-polynomial processing**: some XOR or mixing step before/after the
   CRC that's consistent within a frame type but varies across types
3. **Different frame structures**: the CRC input might depend on the
   command type in a way we haven't identified

To crack fully, either:
- **Firmware disassembly** of the PB554 touchpad
- **Capture ALL command types (pump, light, temp) in a SINGLE session**
  to eliminate session-dependent state
- Use `tools/crack_crc.py` and `tools/debug_crc.py` for further analysis
