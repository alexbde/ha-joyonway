# Joyonway Spa RS-485 Protocol Reference

This document serves as the canonical reference for the RS-485 serial communication protocol used by Joyonway spa controllers sniped from physical touchpad-to-controller buses.

Model-specific registers, status bytes, and command frames are located in their dedicated reference files:
* **[P25B85 Protocol Reference](p25b85_protocol.md)** (Touchpad PB554, single dual-speed pump variant)
* **[P23B32 Protocol Reference](p23b32_protocol.md)** (Touchpads PB554/PB555, dual single-speed pump variant)


## 1. Physical Layer & Framing

The controllers communicate over half-duplex RS-485 using the following serial settings:
* **Baud Rate:** 38400
* **Data Bits:** 8
* **Parity:** None
* **Stop Bits:** 1

### 1.1. Frame Delimiters
All communication on the bus is wrapped in framing boundaries:

| Field | Value |
| :--- | :--- |
| **Start Delimiter** | `0x1A` |
| **End Delimiter** | `0x1D` |
| **Escape Byte** | `0x1B` |

### 1.2. Escape Decoding Table
To prevent payload bytes from colliding with delimiter control bytes, the protocol escapes certain byte values on the wire:

| Wire Pair | Decoded Byte |
| :--- | :--- |
| `1B 11` | `0x1A` |
| `1B 0B` | `0x1B` |
| `1B 13` | `0x1C` |
| `1B 14` | `0x1D` |
| `1B 15` | `0x1E` |

**Decoding Order:** Frame boundaries are processed on the raw wire bytes first by identifying the `0x1A` start delimiter and scanning for the `0x1D` end delimiter. Once the raw frame is isolated, escape decoding is applied to the payload between the delimiters.


## 2. CRC-32 Specification

All command and broadcast frames carry a 4-byte CRC-32. This CRC-32 is used for both outbound command construction and inbound broadcast frame validation.

| Parameter | Value |
| :--- | :--- |
| **Polynomial** | `0x04C11DB7` (standard Ethernet CRC-32) |
| **Initialization Value** | `0xFFFFFFFF` |
| **XorOut Value** | `0x00000000` (None) |
| **Reflection** | No (MSB-first, lookup table driven) |
| **Message Input** | Unescaped payload bytes (any length; padded with trailing zeros to 4-byte boundary) |
| **Preprocessing** | Each 32-bit word is byte-swapped/reversed before the CRC calculation |
| **CRC Storage** | Little-endian, occupying the last 4 bytes of the unescaped inner frame |

### 2.1. Algorithm (Pseudocode)

```
function compute_crc(payload[0..N-1]):
    swapped = word32_swap(payload)   # reverse bytes within each 4-byte group
                                     # (short trailing chunk zero-padded to 4)
    crc = 0xFFFFFFFF
    for each byte b in swapped:
        crc = (crc << 8) XOR TABLE[((crc >> 24) XOR b) AND 0xFF]
        crc = crc AND 0xFFFFFFFF
    return crc
```

*Note: The **word swap** preprocessing is due to the hardware CRC peripheral on the touchpad's ARM Cortex-M MCU feeding little-endian words directly into the calculation.*

### 2.2. Inbound Validation
Broadcast frames are validated by computing the CRC over all unescaped bytes except the last 4 (the CRC field itself). If the calculated value does not match the received CRC, the frame is silently discarded.

### 2.3. Wire Frame Construction (Outbound)
```
inner = payload[16 bytes] + crc[4 bytes LE]
escaped = escape(inner)
wire = 0x1A + escaped + 0x1D
```


## 3. CRC Derivation Notes

This section summarizes how the CRC parameters were derived and confirmed:

* **Discovery of Word Swap:** Delta/linearity checks on captured command frames behaved like a CRC-family transform, but simple brute-forcing failed because byte positions did not align. The discovery that the payload is byte-reversed inside 32-bit words before processing resolved this and unlocked polynomial validation.
* **Polynomial:** Exhaustive search over all 2^32 possible CRC-32 polynomials identified `0x04C11DB7` as the single working polynomial.
* **XorOut Equivalence:** Early command-only analysis used `init=0x00000000` and `xor_out=0x552D22C8` for 16-byte payloads. These parameters are mathematically equivalent to the generalized `init=0xFFFFFFFF` / `xor_out=0x00000000` format when processing the exact same length of inputs. The generalized form is preferred as it functions correctly for arbitrary-length broadcast frame validation.
