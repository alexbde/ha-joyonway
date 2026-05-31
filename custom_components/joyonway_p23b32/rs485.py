# 2026-05-14 | RS485 | Send frames + broadcast read | FIX Python 3.14 asyncio.get_running_loop()
# 2026-05-16 | FIX  | MASK_CHAUFFAGE=0x10 restored, filtration=pb2&MASK_FILTRATION restored
"""RS485 frames for Joyonway P23B32: send commands + read broadcast status."""
from __future__ import annotations
import asyncio
import logging
from .const import (
    CMD_LUMIERE_ON, CMD_LUMIERE_OFF,
    CMD_POMPE_GAUCHE_ON, CMD_POMPE_GAUCHE_OFF,
    CMD_POMPE_DROITE_ON, CMD_POMPE_DROITE_OFF,
    CMD_BULLEUR_ON, CMD_BULLEUR_OFF,
    CMD_FILTRATION, CMD_ALL_OFF, CMD_CONSIGNE,
    TCP_TIMEOUT, REPEAT_COUNT, REPEAT_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# ============================================================
# SEND FRAMES - physically validated 2026-05-11
# ============================================================
FRAMES: dict[str, bytes] = {
    CMD_LUMIERE_ON:       bytes.fromhex("1a0130103ca100a100000040400204000081edbaa01b141d"),
    CMD_LUMIERE_OFF:      bytes.fromhex("1a0130103ca100a1000000404002040000805a20cdc11d"),
    CMD_POMPE_GAUCHE_ON:  bytes.fromhex("1a0130103ca100a10604000002040000008b3ee4131d"),
    CMD_POMPE_GAUCHE_OFF: bytes.fromhex("1a0130103ca100a1060000000204000000" "08bd10331d"),
    CMD_POMPE_DROITE_ON:  bytes.fromhex("1a0130103ca100a1181000000204000000" "40d12de01d"),
    CMD_POMPE_DROITE_OFF: bytes.fromhex("1a0130103ca100a1180000000204000000" "4cdfff631d"),
    CMD_BULLEUR_ON:       bytes.fromhex("1a0130103ca100a1000004040204000000" "0f7f1b11761d"),
    CMD_BULLEUR_OFF:      bytes.fromhex("1a0130103ca100a1000004000204000000" "fcc2864f1d"),
    CMD_FILTRATION:       bytes.fromhex("1a0130103ca400a1620500160017000600" "fc7954c61d"),
    CMD_ALL_OFF:          bytes.fromhex("1a0130083caa0002138ce4268b1d"),
}

def build_consigne_frame(temp_f: int) -> bytes:
    """Build thermostat setpoint frame for a temperature in Fahrenheit."""
    if not 60 <= temp_f <= 104:
        raise ValueError(f"Setpoint out of range: {temp_f}F (60-104)")
    return bytes.fromhex(f"1a0130103ca100a10000000080800204000000{temp_f:02x}009879d0e21d")

# ============================================================
# BROADCAST READ - parse frames received from USR-W610
# ============================================================
# Byte 12 (IDX_PUMP_BYTE1): bit 0x04=pompe_gauche, bit 0x10=pompe_droite
# Byte 14 (IDX_PUMP_BYTE2): bit 0x01=filtration, bit 0x08=bulleur, bit 0x10=chauffage
# Byte 17 (IDX_LIGHT_BYTE): bit 0x01=lumiere
# Note: ozonateur byte not yet identified (help welcome, open an issue)
BROADCAST_SIGNATURE = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x02])
FRAME_MIN_LENGTH = 20
IDX_WATER_TEMP = 9
IDX_PUMP_BYTE1 = 12
IDX_PUMP_BYTE2 = 14
IDX_SETPOINT   = 16
IDX_LIGHT_BYTE = 17
MASK_POMPE_GAUCHE = 0x04
MASK_POMPE_DROITE = 0x10
MASK_FILTRATION   = 0x01
MASK_BULLEUR      = 0x08
# 0x10 confirmed by heater frame: byte14=0x31=0x20+0x10+0x01
MASK_CHAUFFAGE    = 0x10
MASK_LUMIERE      = 0x01

def fahrenheit_to_celsius(f):
    """Convert Fahrenheit to Celsius, return None for invalid values."""
    if f == 0 or f > 200:
        return None
    return round((f - 32) * 5 / 9, 1)

async def read_spa_status(host: str, port: int, timeout: float = 5.0):
    """Read one broadcast status frame from the USR-W610. FIX Python 3.14: get_running_loop()."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("W610 connection failed: %s", err)
        return None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        buf = bytearray()
        while loop.time() < deadline:
            try:
                chunk = await asyncio.wait_for(reader.read(512), timeout=1.0)
                if not chunk:
                    break
                buf.extend(chunk)
                idx = buf.find(BROADCAST_SIGNATURE)
                if idx != -1 and len(buf) >= idx + FRAME_MIN_LENGTH:
                    return _parse(buf, idx)
                if len(buf) > 4096:
                    buf = buf[-1024:]
            except asyncio.TimeoutError:
                continue
        _LOGGER.debug("W610 timeout: %d bytes read, no complete frame", len(buf))
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

def _parse(buf, idx):
    """Decode a broadcast 0xB4 frame starting at signature index."""
    tf  = buf[idx + IDX_WATER_TEMP]
    pb1 = buf[idx + IDX_PUMP_BYTE1]
    pb2 = buf[idx + IDX_PUMP_BYTE2]
    sf  = buf[idx + IDX_SETPOINT]
    lb  = buf[idx + IDX_LIGHT_BYTE]
    return {
        "water_temperature": fahrenheit_to_celsius(tf),
        "setpoint":          fahrenheit_to_celsius(sf),
        "pompe_gauche":      bool(pb1 & MASK_POMPE_GAUCHE),
        "pompe_droite":      bool(pb1 & MASK_POMPE_DROITE),
        "filtration":        bool(pb2 & MASK_FILTRATION),
        "chauffage":         bool(pb2 & MASK_CHAUFFAGE),
        "bulleur":           bool(pb2 & MASK_BULLEUR),
        "lumiere":           bool(lb  & MASK_LUMIERE),
        "raw_pb1": pb1, "raw_pb2": pb2, "raw_lb": lb,
    }

# ============================================================
# SEND COMMANDS
# ============================================================
async def send_command(host: str, port: int, command: str, consigne_f: int | None = None,
                       repeat: int = REPEAT_COUNT, interval: float = REPEAT_INTERVAL) -> bool:
    """Send an RS485 command to the USR-W610 (with repetition for reliability)."""
    if command == CMD_CONSIGNE:
        if consigne_f is None:
            _LOGGER.error("Consigne command missing consigne_f value")
            return False
        try:
            frame = build_consigne_frame(consigne_f)
        except ValueError as err:
            _LOGGER.error("Invalid setpoint: %s", err)
            return False
    elif command in FRAMES:
        frame = FRAMES[command]
    else:
        _LOGGER.error("Unknown command: %s", command)
        return False
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TCP_TIMEOUT
        )
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.error("W610 %s:%s connection failed: %s", host, port, err)
        return False
    try:
        for i in range(repeat):
            writer.write(frame)
            await writer.drain()
            if i < repeat - 1:
                await asyncio.sleep(interval)
        _LOGGER.debug("Command %s sent (%d repetitions, %d bytes)", command, repeat, len(frame))
        return True
    except OSError as err:
        _LOGGER.error("RS485 write error: %s", err)
        return False
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except OSError:
            pass

async def test_connection(host: str, port: int) -> bool:
    """Test TCP connection to the USR-W610 bridge (used by config_flow)."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TCP_TIMEOUT
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError) as err:
        _LOGGER.debug("Connection test %s:%s failed: %s", host, port, err)
        return False
