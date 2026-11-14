"""Dishwasher programme select: list from DOP2 2/1584, write through 2/1577 (#61).

Fixtures are a G7655's reads (XKM EK057S, FW 09.14). Selecting Intensive
from Eco through 2/1577 was answered 204 there, the read-back matched the
write and the panel showed Intensive programmed.
"""
import asyncio
import re
import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from asyncmiele.exceptions.network import NetworkTimeoutError, ResponseError
from homeassistant.exceptions import HomeAssistantError

import custom_components.miele_lan.coordinator as coordinator_mod
from custom_components.miele_lan.api import MieleLanClient
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.coordinator import PROGRAM_LIST_MAX_MISSES
from custom_components.miele_lan.dop2 import _walk_fields, parse_program_list
from custom_components.miele_lan.select import MieleLanProgramSelect, program_option

FIXTURES = Path(__file__).parent / "fixtures"
FAB = "000000000000"
LIST_URL = f"/Devices/{FAB}/DOP2/2/1584?idx1=0&idx2=0"
SELECT_URL = f"/Devices/{FAB}/DOP2/2/1577?idx1=0&idx2=0"
G7655_IDS = [200, 202, 203, 204, 205, 209, 206, 207, 208, 210, 214, 215]


def _hex(name: str) -> bytes:
    return bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", (FIXTURES / name).read_text()))


PROGRAM_LIST = _hex("g7655_dop2_1584.hex")
SELECT_READ = _hex("g7655_dop2_1577_intensive.hex")


# --- 2/1584 parser ----------------------------------------------------------

def test_g7655_program_list() -> None:
    assert parse_program_list(PROGRAM_LIST) == G7655_IDS


def test_valid_elements_limits_the_list() -> None:
    payload = bytearray(PROGRAM_LIST)
    off = 12 + 3  # field 1 ValidElements, U8 value
    assert payload[off] == 12
    payload[off] = 3
    assert parse_program_list(bytes(payload)) == G7655_IDS[:3]


@pytest.mark.parametrize("payload", [b"", b"\x00" * 11, _hex("g7655_dop2_1585.hex"), SELECT_READ])
def test_other_payloads_give_no_list(payload: bytes) -> None:
    assert parse_program_list(payload) == []


def test_every_g7655_programme_has_a_name() -> None:
    assert all(not program_option(i).isdigit() for i in G7655_IDS)
    assert program_option(9999) == "9999"


# --- coordinator: read 2/1584 once, only right after 2/1585 answered ---------

class _Raw:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = list(answers)
        self.resources: list[str] = []

    async def _request_bytes(self, method: str, resource: str, **kwargs: Any):
        self.resources.append(resource)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _coordinator(answers: list[Any], device_type=MieleAppliance.DISHWASHER, fresh=True):
    coord = coordinator_mod.MieleLanCoordinator.__new__(coordinator_mod.MieleLanCoordinator)
    coord.client = SimpleNamespace(raw=_Raw(answers))
    coord.fab = FAB
    coord._data = coordinator_mod.MieleLanData()
    coord._data.ident = {"device_type": int(device_type)}
    coord._device_context_fresh = fresh
    coord._program_list_misses = 0
    return coord


def test_reads_the_list_when_2_1585_just_answered() -> None:
    coord = _coordinator([(200, PROGRAM_LIST)])
    asyncio.run(coord._maybe_fetch_program_list())
    assert coord.client.raw.resources == [LIST_URL]
    assert coord._data.program_list == G7655_IDS


def test_reads_the_list_only_once() -> None:
    coord = _coordinator([(200, PROGRAM_LIST)])
    asyncio.run(coord._maybe_fetch_program_list())
    asyncio.run(coord._maybe_fetch_program_list())
    assert coord.client.raw.resources == [LIST_URL]


def test_never_probes_without_a_fresh_2_1585_answer() -> None:
    """EK037 units time out on leaves above 1000 and stall; they never answer 2/1585."""
    coord = _coordinator([], fresh=False)
    asyncio.run(coord._maybe_fetch_program_list())
    assert coord.client.raw.resources == []


@pytest.mark.parametrize("device_type", [MieleAppliance.WASHING_MACHINE, MieleAppliance.OVEN])
def test_only_dishwashers_read_the_list(device_type) -> None:
    coord = _coordinator([], device_type=device_type)
    asyncio.run(coord._maybe_fetch_program_list())
    assert coord.client.raw.resources == []


@pytest.mark.parametrize("status", [403, 404])
def test_unsupported_leaf_stops_further_reads(status: int) -> None:
    coord = _coordinator([(status, b"")])
    asyncio.run(coord._maybe_fetch_program_list())
    asyncio.run(coord._maybe_fetch_program_list())
    assert coord.client.raw.resources == [LIST_URL]
    assert coord._data.program_list is None


def test_gives_up_after_repeated_misses() -> None:
    answers = [(500, b""), NetworkTimeoutError("timeout"), (200, b"\x00" * 12)]
    assert len(answers) == PROGRAM_LIST_MAX_MISSES
    coord = _coordinator(answers + [(200, PROGRAM_LIST)])
    for _ in range(PROGRAM_LIST_MAX_MISSES + 1):
        asyncio.run(coord._maybe_fetch_program_list())
    assert len(coord.client.raw.resources) == PROGRAM_LIST_MAX_MISSES
    assert coord._data.program_list is None


def test_store_device_context_marks_the_poll_fresh() -> None:
    coord = _coordinator([], fresh=False)
    coord._store_device_context(_hex("g7655_dop2_1585.hex"))
    assert coord._device_context_fresh is True


# --- client.select_program ---------------------------------------------------

def _client(answers: list[Any]) -> MieleLanClient:
    return MieleLanClient(_Raw(answers), route=FAB)


def _prog_id(payload: bytes) -> int:
    fields, _ = _walk_fields(payload, 12, struct.unpack_from(">H", payload, 10)[0])
    return fields[1][1]


def test_select_writes_the_patched_read_back() -> None:
    calls: list[tuple] = []

    class _Recorder(_Raw):
        async def _request_bytes(self, method, resource, **kwargs):
            calls.append((method, resource, kwargs.get("body")))
            return await super()._request_bytes(method, resource, **kwargs)

    client = MieleLanClient(_Recorder([(200, SELECT_READ), (204, b"")]), route=FAB)
    asyncio.run(client.select_program(200))
    assert [(m, r) for m, r, _ in calls] == [("GET", SELECT_URL), ("PUT", SELECT_URL)]
    assert _prog_id(calls[1][2]) == 200


def test_select_read_failure_writes_nothing() -> None:
    client = _client([ResponseError(500, "asleep")])
    with pytest.raises(HomeAssistantError, match="HTTP 500"):
        asyncio.run(client.select_program(200))
    assert client._client.resources == [SELECT_URL]


def test_select_unexpected_layout_writes_nothing() -> None:
    client = _client([(200, PROGRAM_LIST)])
    with pytest.raises(HomeAssistantError, match="Unexpected programme selection data"):
        asyncio.run(client.select_program(200))
    assert client._client.resources == [SELECT_URL]


def test_select_refused_write_names_remote_control() -> None:
    client = _client([(200, SELECT_READ), ResponseError(403, "refused")])
    with pytest.raises(HomeAssistantError, match="Remote control was refused"):
        asyncio.run(client.select_program(200))


# --- select entity -----------------------------------------------------------

def _entity(state: dict[str, Any], program_list=G7655_IDS):
    client = SimpleNamespace(route=FAB, select_program=AsyncMock())
    coord = SimpleNamespace(
        client=client,
        data=SimpleNamespace(program_list=program_list, state=state, ident={}),
        async_request_refresh=AsyncMock(),
    )
    return MieleLanProgramSelect(coord), coord


def test_options_follow_the_appliance_list() -> None:
    entity, _ = _entity({})
    assert entity.options == [program_option(i) for i in G7655_IDS]
    assert entity.options[:2] == ["eco", "automatic"]


@pytest.mark.parametrize(
    "program_id,expected",
    [(205, "intensive"), (200, "eco"), (0, None), (9999, None), (None, None), (True, None)],
)
def test_current_option_follows_state_program_id(program_id, expected) -> None:
    entity, _ = _entity({"ProgramID": program_id})
    assert entity.current_option == expected


@pytest.mark.parametrize("status", [2, 3])
def test_select_writes_while_on_or_programmed(status: int) -> None:
    entity, coord = _entity({"Status": status, "ProgramID": 200})
    asyncio.run(entity.async_select_option("intensive"))
    coord.client.select_program.assert_awaited_once_with(205)
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize("status", [1, 4, 5, 6, 7, None])
def test_select_is_refused_in_other_states(status) -> None:
    entity, coord = _entity({"Status": status})
    with pytest.raises(HomeAssistantError, match="only be selected"):
        asyncio.run(entity.async_select_option("intensive"))
    coord.client.select_program.assert_not_awaited()


def test_select_is_added_once_when_the_list_arrives_after_setup() -> None:
    from custom_components.miele_lan.select import _program_select_adder

    _, coord = _entity({}, program_list=None)
    added: list[list] = []
    adder = _program_select_adder(coord, added.append)
    adder()
    assert added == []
    coord.data.program_list = G7655_IDS
    adder()
    adder()
    assert len(added) == 1 and isinstance(added[0][0], MieleLanProgramSelect)
