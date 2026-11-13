"""Programme selection: read-modify-write of DOP2 2/1577 GLOBAL_PS_Select (#61).

The fixture is a G7655's 2/1577 read (XKM EK057S, FW 09.14) with Intensive
(205) selected. The write must keep every byte as read except attr 1 ProgId
and attr 3 SelectionType, the same as the app's laundry SelectProgramAction.
"""
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.miele_lan.api import build_program_select_payload
from custom_components.miele_lan.dop2 import _walk_fields
from custom_components.miele_lan.services import (
    ATTR_DEVICE_ID,
    ATTR_PROGRAM_ID,
    SELECT_PROGRAM_SCHEMA,
    _select_program,
)

READ = bytes.fromhex(
    (Path(__file__).parent / "fixtures" / "g7655_dop2_1577_intensive.hex").read_text().strip()
)
ECO = 200
INTENSIVE = 205


def _fields(payload: bytes) -> dict:
    count = struct.unpack_from(">H", payload, 10)[0]
    fields, end = _walk_fields(payload, 12, count)
    assert end == len(payload)
    return fields


def test_payload_sets_prog_id_and_selection_type() -> None:
    fields = _fields(build_program_select_payload(READ, ECO))
    assert fields[1] == (0x07, ECO)
    assert fields[3] == (0x04, 0)


def test_payload_keeps_every_other_byte() -> None:
    payload = build_program_select_payload(READ, ECO)
    assert len(payload) == len(READ)
    changed = [i for i, (a, b) in enumerate(zip(READ, payload)) if a != b]
    assert changed == [16]  # low byte of ProgId: 0x00cd -> 0x00c8


def test_payload_resets_a_non_zero_selection_type() -> None:
    read = bytearray(READ)
    read[20] = 2  # SelectionType PARAMETRIZATION
    assert _fields(build_program_select_payload(bytes(read), INTENSIVE))[3] == (0x04, 0)


def test_payload_drops_trailing_bytes_beyond_the_declared_length() -> None:
    assert build_program_select_payload(READ + b"\x20" * 10, ECO) == build_program_select_payload(
        READ, ECO
    )


@pytest.mark.parametrize(
    "read",
    [
        READ[:11],
        bytes.fromhex("0054000206300000") + READ[8:],  # 2/1584, not 2/1577
        READ[:-1],  # shorter than the declared length
        READ[:12] + READ[17:],  # attr 1 missing
    ],
)
def test_payload_rejects_anything_but_a_whole_ps_select(read: bytes) -> None:
    with pytest.raises(ValueError):
        build_program_select_payload(read, ECO)


def test_payload_rejects_out_of_range_program_id() -> None:
    with pytest.raises(ValueError):
        build_program_select_payload(READ, 0x10000)


def test_schema() -> None:
    data = SELECT_PROGRAM_SCHEMA({ATTR_DEVICE_ID: "abc", ATTR_PROGRAM_ID: "200"})
    assert data[ATTR_PROGRAM_ID] == 200


class _Raw:
    def __init__(self, answers: list[tuple[int, bytes]]) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[str, str, bytes | None]] = []

    async def _request_bytes(self, method: str, resource: str, *, body=None, **kwargs):
        self.requests.append((method, resource, body))
        return self.answers.pop(0)


def _coordinator(raw: _Raw):
    return SimpleNamespace(fab="000000000001", client=SimpleNamespace(raw=raw))


@pytest.mark.asyncio
async def test_select_program_reads_writes_and_reads_back() -> None:
    after = build_program_select_payload(READ, ECO)
    raw = _Raw([(200, READ), (200, b""), (200, after)])
    result = await _select_program(_coordinator(raw), ECO)

    resource = "/Devices/000000000001/DOP2/2/1577?idx1=0&idx2=0"
    assert [(m, r) for m, r, _ in raw.requests] == [
        ("GET", resource), ("PUT", resource), ("GET", resource),
    ]
    assert raw.requests[1][2] == after
    assert result["program_id_before"] == INTENSIVE
    assert result["status"] == 200
    assert result["program_id_after"] == ECO


@pytest.mark.asyncio
async def test_select_program_reports_a_refused_write() -> None:
    raw = _Raw([(200, READ), (403, b""), (200, READ)])
    result = await _select_program(_coordinator(raw), ECO)
    assert result["status"] == 403
    assert result["program_id_after"] == INTENSIVE


@pytest.mark.asyncio
async def test_select_program_never_writes_without_a_read() -> None:
    from homeassistant.exceptions import HomeAssistantError

    raw = _Raw([(500, b"")])
    with pytest.raises(HomeAssistantError, match="HTTP 500"):
        await _select_program(_coordinator(raw), ECO)
    assert [m for m, _, _ in raw.requests] == ["GET"]
