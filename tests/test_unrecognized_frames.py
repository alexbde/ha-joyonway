# ruff: noqa: E402
"""Tests for unrecognized-broadcast diagnostics (regression: issue #80).

A controller board version that emits a CRC-valid broadcast frame
with an unknown header must not be dropped silently. These tests lock in:
- adapter signature matching is public, length-safe and shared with the config flow
- the coordinator warns (throttled) and counts unrecognized frames
- UpdateFailed carries an actionable reason
- RX staleness tracks any inbound bytes, not just parsed broadcasts
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("homeassistant")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.exceptions import ConfigEntryError

from custom_components.joyonway.adapters import ADAPTERS, get_adapter
from custom_components.joyonway.adapters.base import MIN_SIGNATURE_LENGTH
from custom_components.joyonway.config_flow import (
    DetectionResult,
    JoyonwayConfigFlow,
    _detect_model,
    _match_model,
)
from custom_components.joyonway.const import UNRECOGNIZED_FRAME_LOG_INTERVAL
from custom_components.joyonway.coordinator import JoyonwayCoordinator
from custom_components.joyonway.protocol import (
    SYNC_FRAME,
    build_frame,
    is_broadcast,
    unescape_frame,
    validate_frame,
)

# Synthetic P25 broadcast payload (frame body without start byte, CRC and end
# byte). Frame index i maps to payload index i - 1. Datetime bytes are left
# zeroed so the frame carries no date information.
_PAYLOAD_LENGTH = 60
_IDX_CURRENT_TEMP = 9
_IDX_JET_BYTE = 12
_IDX_HEATER_STATE = 14
_IDX_SETPOINT = 16
_IDX_LIGHT_CYCLE = 17


def _build_broadcast(**overrides: int) -> bytes:
    """Build a wire-ready, CRC-valid P25B85 broadcast frame.

    Built from scratch rather than a capture file so the test is hermetic
    (``tools/captures/`` is gitignored and absent in CI).
    """
    payload = bytearray(_PAYLOAD_LENGTH)
    # Bytes 1..8 of the frame make up the P25 family signature.
    payload[0:8] = bytes([0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x08, 0x03])
    payload[_IDX_CURRENT_TEMP - 1] = 0x63  # 99 F
    payload[_IDX_JET_BYTE - 1] = 0x00
    payload[_IDX_HEATER_STATE - 1] = 0x40  # P25B85 "off"
    payload[_IDX_SETPOINT - 1] = 0x64  # 100 F
    payload[_IDX_LIGHT_CYCLE - 1] = 0x00
    for frame_index, value in overrides.items():
        payload[int(frame_index) - 1] = value
    return build_frame(bytes(payload))


def _reference_broadcast() -> bytes:
    """Return a valid P25B85 broadcast frame."""
    return _build_broadcast()


def _variant_broadcast(index: int = 7, value: int = 0x09) -> bytes:
    """Build a CRC-valid broadcast frame with an unknown header byte.

    Default is byte 7 = 0x09, i.e. a recognized P25 controller reporting an
    unverified board version (v1.9).
    """
    return _build_broadcast(**{str(index): value})


def _foreign_broadcast() -> bytes:
    """Build a CRC-valid broadcast frame belonging to no known model family."""
    return _build_broadcast(**{"8": 0x99})


class FakeHass:
    def __init__(self):
        self.config_entries = MagicMock()

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class FakeEntry:
    def __init__(self):
        self.entry_id = "test_entry"
        self.data = {CONF_HOST: "127.0.0.1", CONF_PORT: 8899}
        self.options = {}

    def async_on_unload(self, func):
        pass


@pytest.fixture
def coordinator():
    coord = JoyonwayCoordinator(FakeHass(), "127.0.0.1", 8899, "P25B85", FakeEntry())
    coord._sync_timeout = 0.0
    return coord


# ── Adapter signature matching ───────────────────────────────────────


@pytest.mark.parametrize("model", sorted(ADAPTERS))
def test_matches_signature_is_length_safe(model: str) -> None:
    """Short frames never raise IndexError, they just don't match."""
    adapter = get_adapter(model)
    for length in range(MIN_SIGNATURE_LENGTH):
        assert (
            adapter.matches_signature(b"\x1a\xff\x01\x3c\xd2\xb4\xff\x08"[:length])
            is False
        )


@pytest.mark.parametrize("model", sorted(ADAPTERS))
def test_matches_signature_accepts_own_signature(model: str) -> None:
    """Every adapter still accepts its own documented signature."""
    adapter = get_adapter(model)
    sig = bytes(adapter.broadcast_signature)
    header = sig if len(sig) >= MIN_SIGNATURE_LENGTH else sig + b"\x08\x01"
    assert adapter.matches_signature(header) is True


def test_p20_accepts_both_board_versions() -> None:
    """P20B29 keeps accepting board versions 1.6 and 1.8, and rejects others."""
    adapter = get_adapter("P20B29")
    for byte7, expected in ((0x06, True), (0x08, True), (0x07, False)):
        header = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, byte7, 0x01])
        assert adapter.matches_signature(header) is expected


@pytest.mark.parametrize("model", sorted(ADAPTERS))
def test_board_version_byte_is_matched_leniently(model: str) -> None:
    """Byte 7 is the board firmware version, not part of the model identity.

    Regression for issue #80: a P25 controller running board version 1.6
    broadcasts 0x06 at index 7 instead of the reference unit's 0x08 (board
    version 1.8), with a byte-for-byte identical payload layout.
    """
    adapter = get_adapter(model)
    family_byte = bytes(adapter.broadcast_signature)[8]
    for byte7, expected in ((0x06, True), (0x08, True), (0x07, False)):
        header = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, byte7, family_byte])
        assert adapter.matches_signature(header) is expected


def test_family_byte_still_distinguishes_models() -> None:
    """Relaxing byte 7 must not let an adapter claim another family's frames."""
    for model, foreign_family in (("P25B85", 0x02), ("P23B32", 0x03), ("P20B29", 0x03)):
        header = bytes([0x1A, 0xFF, 0x01, 0x3C, 0xD2, 0xB4, 0xFF, 0x06, foreign_family])
        assert get_adapter(model).matches_signature(header) is False


# Verbatim broadcast frame reported in issue #80 by a P25 controller running
# board version 1.6 (byte 7 = 0x06). CRC-valid, 66 bytes, identical payload
# layout to the reference unit running board version 1.8.
_ISSUE_80_FRAME = bytes.fromhex(
    "1aff013cd2b4ff06034e0406007d40003b00000a000c000d001500000048000a0052"
    "0014000000064d0000000000000000000000001a071d1717110300892529b81d"
)


def test_issue_80_frame_parses_with_p25_byte_map() -> None:
    """The reported board-v1.6 frame parses correctly with the P25 byte map."""
    assert is_broadcast(_ISSUE_80_FRAME)
    assert validate_frame(_ISSUE_80_FRAME, unescape_full=True)

    logical = unescape_frame(_ISSUE_80_FRAME, unescape_full=True)
    data = get_adapter("P25B85").parse_status(logical)

    assert data is not None
    assert data["current_temperature"] == 26  # 78 F
    assert data["setpoint"] == 15  # 59 F
    assert data["status"] == "off"
    assert data["jets"] == "off"
    # Schedule slots and the on-board clock decode to plausible values, which
    # confirms the payload layout is unchanged across board versions.
    assert data["filter_slot1_start"] == (8, 0)
    assert data["filter_slot1_end"] == (10, 0)
    assert data["spa_datetime"] is not None


def test_issue_80_frame_is_detected_as_p25() -> None:
    """Config-flow detection resolves the reported frame to the P25 family."""
    assert _match_model(_ISSUE_80_FRAME) == "P25B85"


def test_reference_broadcast_still_parses() -> None:
    """A well-formed P25B85 broadcast is unaffected by the signature refactor."""
    adapter = get_adapter("P25B85")
    frame = _reference_broadcast()
    assert is_broadcast(frame)
    assert validate_frame(frame, unescape_full=True)

    logical = unescape_frame(frame, unescape_full=True)
    data = adapter.parse_status(logical)
    assert data is not None
    assert data["current_temperature"] == 37  # 99 F
    assert data["setpoint"] == 38  # 100 F
    assert data["status"] == "off"


def test_variant_broadcast_is_valid_but_unparseable() -> None:
    """An unverified board version passes CRC yet matches no adapter.

    This is what made the failure invisible: transport-level checks all pass.
    """
    frame = _variant_broadcast()
    assert is_broadcast(frame)
    assert validate_frame(frame, unescape_full=True)

    logical = unescape_frame(frame, unescape_full=True)
    assert logical[:9].hex(" ") == "1a ff 01 3c d2 b4 ff 09 03"
    assert get_adapter("P25B85").parse_status(logical) is None
    assert get_adapter("P25B85").unsupported_board_version(logical) == 0x09


def test_foreign_family_is_not_reported_as_board_version() -> None:
    """A different model family must not be blamed on the board version."""
    logical = unescape_frame(_foreign_broadcast(), unescape_full=True)
    assert get_adapter("P25B85").unsupported_board_version(logical) is None


# ── Config flow / runtime agreement ──────────────────────────────────


def test_match_model_uses_adapter_signature() -> None:
    """Detection resolves real frames and rejects unknown header variants."""
    assert _match_model(_reference_broadcast()) == "P25B85"
    assert _match_model(_variant_broadcast()) is None


@pytest.mark.asyncio
async def test_detect_model_reports_unsupported_board_version() -> None:
    """An unverified board version is reported explicitly, not guessed at."""
    reader = MagicMock()
    reader.read = AsyncMock(return_value=_variant_broadcast())
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    with patch("asyncio.open_connection", return_value=(reader, writer)):
        result = await _detect_model("127.0.0.1", 8899)

    assert result.connected is True
    assert result.model is None
    assert result.unsupported_model == "P25B85"
    assert result.board_version == "1.9"


@pytest.mark.asyncio
async def test_detect_model_rejects_foreign_family() -> None:
    """A frame from no known family falls through to manual model selection."""
    reader = MagicMock()
    reader.read = AsyncMock(return_value=_foreign_broadcast())
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    with patch("asyncio.open_connection", return_value=(reader, writer)):
        result = await _detect_model("127.0.0.1", 8899)

    assert result.connected is True
    assert result.model is None
    assert result.board_version is None


@pytest.mark.asyncio
async def test_detect_model_accepts_reference_frame() -> None:
    """A genuine P25B85 broadcast frame is still auto-detected."""
    reader = MagicMock()
    reader.read = AsyncMock(return_value=_reference_broadcast())
    writer = MagicMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()

    with patch("asyncio.open_connection", return_value=(reader, writer)):
        result = await _detect_model("127.0.0.1", 8899)

    assert result.model == "P25B85"


@pytest.mark.asyncio
async def test_config_flow_aborts_on_unsupported_board_version() -> None:
    """Setup stops with a user-facing message naming the board version."""
    flow = JoyonwayConfigFlow()
    flow.hass = MagicMock()
    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()

    with patch(
        "custom_components.joyonway.config_flow._detect_model",
        return_value=DetectionResult(
            connected=True, unsupported_model="P25B85", board_version="1.9"
        ),
    ):
        result = await flow.async_step_user({CONF_HOST: "127.0.0.1", CONF_PORT: 8899})

    assert result["type"] == "abort"
    assert result["reason"] == "unsupported_board_version"
    assert result["description_placeholders"]["version"] == "1.9"
    assert result["description_placeholders"]["model"] == "P25B85"
    assert (
        result["description_placeholders"]["issues_url"]
        == "https://github.com/alexbde/ha-joyonway/issues"
    )


# ── Coordinator frame accounting & logging ───────────────────────────


def test_unrecognized_broadcast_is_counted_and_logged(coordinator, caplog) -> None:
    """A CRC-valid but unparseable broadcast warns instead of vanishing."""
    foreign = _foreign_broadcast()
    with caplog.at_level("WARNING"):
        data, consumed = coordinator._try_parse_buffer(foreign)

    assert data is None
    assert consumed == len(foreign)
    assert coordinator.rx_frame_stats["broadcast"] == 1
    assert coordinator.rx_frame_stats["unrecognized"] == 1
    assert coordinator.rx_frame_stats["crc_error"] == 0
    assert coordinator.rx_frame_stats["parsed"] == 0
    assert "cannot" in caplog.text and "P25B85" in caplog.text
    assert coordinator.last_unrecognized_frame is not None


def test_unsupported_board_version_is_reported(coordinator, caplog) -> None:
    """An unverified board version fails loudly, naming the version."""
    variant = _variant_broadcast()
    with caplog.at_level("ERROR"):
        data, _ = coordinator._try_parse_buffer(variant)

    assert data is None
    assert coordinator.unsupported_board_version == "1.9"
    assert "Unsupported controller board version 1.9" in caplog.text
    assert "github.com/alexbde/ha-joyonway/issues" in caplog.text

    reason = coordinator._no_data_reason()
    assert "Unsupported controller board version 1.9" in reason
    assert "0x09" in reason
    assert "open an issue" in reason


@pytest.mark.asyncio
async def test_unsupported_board_version_raises_config_entry_error(
    coordinator,
) -> None:
    """Setup must fail permanently rather than retry an unsupported board."""
    coordinator._try_parse_buffer(_variant_broadcast())
    coordinator._connect = AsyncMock()

    with pytest.raises(ConfigEntryError, match="Unsupported controller board version"):
        await coordinator._async_update_data()


def test_unrecognized_broadcast_warning_is_throttled(coordinator, caplog) -> None:
    """The spa broadcasts ~2x/sec — the warning must not spam the log."""
    logical = unescape_frame(_foreign_broadcast(), unescape_full=True)
    with caplog.at_level("WARNING"):
        for _ in range(50):
            coordinator._log_unrecognized_broadcast(logical)

    assert caplog.text.count("cannot") == 1
    assert UNRECOGNIZED_FRAME_LOG_INTERVAL > 0


def test_sync_and_unicast_frames_are_counted(coordinator) -> None:
    """Sync and per-peripheral unicast frames are tracked, not silently dropped."""
    unicast = bytes([0x1A, 0x20, 0x01, 0x0C, 0xC3, 0xA5, 0x00, 0x00, 0x1D])
    coordinator._try_parse_buffer(SYNC_FRAME + unicast + SYNC_FRAME)

    assert coordinator.rx_frame_stats["sync"] == 2
    assert coordinator.rx_frame_stats["unicast"] == 1


def test_valid_broadcast_increments_parsed(coordinator) -> None:
    """A good frame is counted as parsed and returned."""
    data, _ = coordinator._try_parse_buffer(_reference_broadcast())
    assert data is not None
    assert coordinator.rx_frame_stats["parsed"] == 1
    assert coordinator.rx_frame_stats["unrecognized"] == 0


# ── Actionable failure reasons ───────────────────────────────────────


@pytest.mark.parametrize(
    ("stats", "expected"),
    [
        ({}, "no RS485 frames received at all"),
        ({"sync": 5}, "bus traffic is flowing"),
        ({"unicast": 5}, "bus traffic is flowing"),
        ({"broadcast": 2}, "none could be parsed"),
        ({"broadcast": 2, "crc_error": 2}, "failed CRC validation"),
        ({"broadcast": 2, "unrecognized": 2}, "cannot parse"),
    ],
)
def test_no_data_reason(coordinator, stats: dict, expected: str) -> None:
    """UpdateFailed explains *why* no data was produced."""
    coordinator._rx_frame_stats.update(stats)
    assert expected in coordinator._no_data_reason()


# ── RX staleness tracking ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reader_loop_marks_rx_on_unparsed_bytes(coordinator) -> None:
    """Bytes that yield no parsed status still prove the link is alive.

    Otherwise a spa whose broadcasts are unparseable would be reconnected
    every RX_STALE_SECONDS forever.
    """
    reader = MagicMock()
    reader.read = AsyncMock(side_effect=[SYNC_FRAME, b""])
    coordinator._reader = reader
    coordinator._stopped = True  # prevent reconnect scheduling in finally

    assert coordinator._last_rx_ts == 0.0
    await coordinator._reader_loop()

    assert coordinator._last_rx_ts > 0.0
    assert coordinator.rx_frame_stats["sync"] == 1
    assert coordinator.data is None
