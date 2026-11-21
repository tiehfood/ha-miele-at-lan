"""Dishwasher door from DOP2 2/1585 when /State.SignalDoor stays false (#59).

The G7197 (EK057S, FW 09.14) never sets SignalDoor over the LAN, but
GLOBAL_DeviceAttributesDWTDWM field 1 (DoorState) follows the door. The
fixtures are its two reads, door open and door closed.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

import custom_components.miele_lan.coordinator as coordinator_mod
from custom_components.miele_lan.binary_sensor import _door_open
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.coordinator import (
    DEVICE_CONTEXT_REFRESH_INTERVAL,
    MieleLanCoordinator,
    MieleLanData,
)
from custom_components.miele_lan.dop2 import parse_global_device_context

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return bytes.fromhex((FIXTURES / name).read_text().strip())


DOOR_OPEN = _fixture("g7197_dop2_1585_door_open.hex")
DOOR_CLOSED = _fixture("g7197_dop2_1585_door_closed.hex")
# G7650 SCVi (EK057S, FW 09.14), SignalDoor false in every read:
# off with the door shut (identical bytes off with the door open), on with
# the door open, and running with the door shut.
G7650_OFF = _fixture("g7650_dop2_1585_off_door_closed.hex")
G7650_ON_OPEN = _fixture("g7650_dop2_1585_on_door_open.hex")
G7650_RUNNING_CLOSED = _fixture("g7650_dop2_1585_running_door_closed.hex")


# ------------------------------------------------------------------ parser
def test_door_state_open() -> None:
    assert parse_global_device_context(DOOR_OPEN)["door_state"] == 1


def test_door_state_closed() -> None:
    assert parse_global_device_context(DOOR_CLOSED)["door_state"] == 2


# ------------------------------------------------------------- door sensor
def test_door_open_from_door_state_when_signal_door_is_dead() -> None:
    data = MieleLanData(state={"SignalDoor": False}, door_state=1)
    assert _door_open(data) is True


def test_door_closed_from_door_state() -> None:
    data = MieleLanData(state={"SignalDoor": False}, door_state=2)
    assert _door_open(data) is False


def test_signal_door_still_counts_without_door_state() -> None:
    data = MieleLanData(state={"SignalDoor": True})
    assert _door_open(data) is True


def test_g7650_door_state_reads() -> None:
    assert parse_global_device_context(G7650_OFF)["door_state"] == 1
    assert parse_global_device_context(G7650_ON_OPEN)["door_state"] == 1
    assert parse_global_device_context(G7650_RUNNING_CLOSED)["door_state"] == 2


def test_door_state_ignored_while_off() -> None:
    """Off, 2/1585 still says open from when the machine was switched off."""
    ctx = parse_global_device_context(G7650_OFF)
    data = MieleLanData(state={"SignalDoor": False, "Status": 1}, door_state=ctx["door_state"])
    assert _door_open(data) is False


def test_door_state_counts_while_on() -> None:
    ctx = parse_global_device_context(G7650_ON_OPEN)
    data = MieleLanData(state={"SignalDoor": False, "Status": 2}, door_state=ctx["door_state"])
    assert _door_open(data) is True
    ctx = parse_global_device_context(G7650_RUNNING_CLOSED)
    data = MieleLanData(state={"SignalDoor": False, "Status": 5}, door_state=ctx["door_state"])
    assert _door_open(data) is False


def test_signal_door_still_counts_while_off() -> None:
    data = MieleLanData(state={"SignalDoor": True, "Status": 1}, door_state=2)
    assert _door_open(data) is True


# ------------------------------------------------------------- coordinator
class _Raw:
    """Replays (status, body) answers for 2/1585 and records every request."""

    def __init__(self, answers: list[tuple[int, bytes]]) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def _request_bytes(self, method: str, resource: str, **kwargs):
        self.calls += 1
        return self.answers.pop(0)


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(coordinator_mod.time, "monotonic", lambda: now["t"])
    monkeypatch.setattr(coordinator_mod.asyncio, "sleep", AsyncMock())
    return now


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch, clock):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(device_type: MieleAppliance, answers: list[tuple[int, bytes]]):
        client = SimpleNamespace(raw=_Raw(answers), wake=AsyncMock(return_value={}))
        coord = MieleLanCoordinator(
            HomeAssistant(str(tmp_path)),
            SimpleNamespace(entry_id="test"),
            client,
            "000000000001",
        )
        coord._data.ident = {"device_type": int(device_type)}
        return coord

    return make


@pytest.mark.asyncio
async def test_dishwasher_reads_door_on_every_poll_once_supported(make_coordinator, clock):
    coord = make_coordinator(MieleAppliance.DISHWASHER, [(200, DOOR_CLOSED), (200, DOOR_OPEN)])
    await coord._maybe_refresh_device_context()
    assert coord._data.door_state == 2

    clock["t"] += 60  # next poll, well inside the 10-minute cadence
    await coord._maybe_refresh_device_context()
    assert coord.client.raw.calls == 2
    assert coord._data.door_state == 1


@pytest.mark.asyncio
async def test_quick_read_never_wakes_and_never_counts_a_miss(make_coordinator, clock):
    coord = make_coordinator(MieleAppliance.DISHWASHER, [(200, DOOR_OPEN)] + [(500, b"")] * 5)
    await coord._maybe_refresh_device_context()
    for _ in range(5):
        clock["t"] += 60
        await coord._maybe_refresh_device_context()
    coord.client.wake.assert_not_awaited()
    assert coord._device_context_miss_cycles == 0
    assert coord._device_context_unsupported is False


@pytest.mark.asyncio
async def test_door_state_is_dropped_when_this_poll_could_not_read_it(make_coordinator, clock):
    coord = make_coordinator(MieleAppliance.DISHWASHER, [(200, DOOR_OPEN), (500, b"")])
    await coord._maybe_refresh_device_context()
    assert coord._data.door_state == 1

    clock["t"] += 60
    await coord._maybe_refresh_device_context()
    assert coord._data.door_state is None
    # The consumable levels stay until the full read on the slow cadence.
    assert coord._data.device_context


@pytest.mark.asyncio
async def test_no_quick_read_before_the_leaf_ever_answered(make_coordinator, clock):
    # A leaf that times out (EK037 FW 04.05) must not cost a stall every poll.
    coord = make_coordinator(MieleAppliance.DISHWASHER, [(500, b""), (500, b"")])
    await coord._maybe_refresh_device_context()
    calls = coord.client.raw.calls
    assert calls == 2  # full read: 500, wake, 500

    clock["t"] += 60
    await coord._maybe_refresh_device_context()
    assert coord.client.raw.calls == calls


@pytest.mark.asyncio
async def test_laundry_keeps_the_slow_cadence(make_coordinator, clock):
    coord = make_coordinator(MieleAppliance.WASHING_MACHINE, [(200, DOOR_CLOSED)])
    await coord._maybe_refresh_device_context()

    clock["t"] += 60
    await coord._maybe_refresh_device_context()
    assert coord.client.raw.calls == 1
    assert coord._data.door_state is None


@pytest.mark.asyncio
async def test_full_read_still_runs_on_the_slow_cadence(make_coordinator, clock):
    coord = make_coordinator(
        MieleAppliance.DISHWASHER, [(200, DOOR_CLOSED), (500, b""), (200, DOOR_OPEN)]
    )
    await coord._maybe_refresh_device_context()

    clock["t"] += DEVICE_CONTEXT_REFRESH_INTERVAL  # due: wake and retry path
    await coord._maybe_refresh_device_context()
    coord.client.wake.assert_awaited_once()
    assert coord._data.door_state == 1
