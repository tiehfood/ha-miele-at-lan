"""Tests for power via DOP2 leaf 2/1586 (issue #16).

No HA runtime, no network — same stub pattern as
tests/test_write_user_request_errors.py / tests/test_process_action_controls.py.
Covers the wire payload, the wake-and-retry-then-fallback flow, and the power
switch's wiring to the new client method.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asyncmiele.exceptions.network import (  # noqa: E402
    NetworkTimeoutError,
    ResponseError,
)
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.miele_lan.api import (  # noqa: E402
    MieleLanClient,
    build_appliance_state_payload,
)
from custom_components.miele_lan.switch import SWITCH_TYPES  # noqa: E402

DOP2_1586_RESOURCE = "/Devices/000000000000/DOP2/2/1586?idx1=0&idx2=0"
DOP2_1583_RESOURCE = "/Devices/000000000000/DOP2/2/1583?idx1=0&idx2=0"
STATE_RESOURCE = "/Devices/000000000000/State"


# --- builder -------------------------------------------------------------


def test_appliance_state_on_matches_hardware_capture() -> None:
    payload = build_appliance_state_payload(True)
    assert (
        payload.hex()
        == "000e000206320000000000010001040420202020202020202020202020202020"
    )
    assert len(payload) == 32


def test_appliance_state_off_matches_hardware_capture() -> None:
    payload = build_appliance_state_payload(False)
    assert (
        payload.hex()
        == "000e000206320000000000010001040120202020202020202020202020202020"
    )


# --- client flow -----------------------------------------------------------


class _QueuedRawClient:
    """Stub replacing the asyncmiele MieleClient underneath MieleLanClient.

    Each queued entry is either a status code to succeed with, or an
    exception instance to raise, consumed in call order regardless of
    resource.
    """

    def __init__(self, responses: list[Any]) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self._responses = list(responses)

    async def _request_bytes(self, method, resource, *, body=None, allowed_status=(200,)):
        self.calls.append((method, resource, body))
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome, b""


def _run(coro):
    return asyncio.run(coro)


def test_success_on_first_try_no_wake_no_fallback() -> None:
    stub = _QueuedRawClient([204])
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(True))
    assert [c[1] for c in stub.calls] == [DOP2_1586_RESOURCE]


def test_500_then_wake_then_retry_204_no_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("custom_components.miele_lan.api.asyncio.sleep", _fake_sleep)

    stub = _QueuedRawClient([ResponseError(500, "asleep"), 200, 204])
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(True))

    assert [c[1] for c in stub.calls] == [
        DOP2_1586_RESOURCE,
        STATE_RESOURCE,
        DOP2_1586_RESOURCE,
    ]
    assert sleeps == [3]


def test_500_then_wake_then_retry_500_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("custom_components.miele_lan.api.asyncio.sleep", _fake_sleep)

    stub = _QueuedRawClient(
        [ResponseError(500, "asleep"), 200, ResponseError(500, "still asleep"), 204]
    )
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(False))

    assert [c[1] for c in stub.calls] == [
        DOP2_1586_RESOURCE,
        STATE_RESOURCE,
        DOP2_1586_RESOURCE,
        DOP2_1583_RESOURCE,
    ]


def test_404_on_first_try_no_wake_falls_back() -> None:
    stub = _QueuedRawClient([ResponseError(404, "not found"), 204])
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(True))

    assert [c[1] for c in stub.calls] == [DOP2_1586_RESOURCE, DOP2_1583_RESOURCE]


def test_network_timeout_on_first_try_falls_back() -> None:
    stub = _QueuedRawClient([NetworkTimeoutError("timed out"), 204])
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(True))

    assert [c[1] for c in stub.calls] == [DOP2_1586_RESOURCE, DOP2_1583_RESOURCE]


def test_both_paths_fail_raises_mapped_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("custom_components.miele_lan.api.asyncio.sleep", _fake_sleep)

    stub = _QueuedRawClient(
        [
            ResponseError(500, "asleep"),
            200,
            ResponseError(500, "still asleep"),
            ResponseError(500, "fallback also fails"),
        ]
    )
    client = MieleLanClient(stub, route="000000000000")
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(client.set_power(True))
    assert "500" in str(exc_info.value)


def test_wake_raising_still_retries_then_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr("custom_components.miele_lan.api.asyncio.sleep", _fake_sleep)

    stub = _QueuedRawClient(
        [
            ResponseError(500, "asleep"),
            ResponseError(500, "wake also fails"),
            ResponseError(500, "retry still fails"),
            204,
        ]
    )
    client = MieleLanClient(stub, route="000000000000")
    _run(client.set_power(True))

    assert [c[1] for c in stub.calls] == [
        DOP2_1586_RESOURCE,
        STATE_RESOURCE,
        DOP2_1586_RESOURCE,
        DOP2_1583_RESOURCE,
    ]


# --- switch wiring -----------------------------------------------------------


def test_power_switch_turn_on_calls_set_power_true() -> None:
    calls: list[bool] = []

    class _FakeClient:
        async def set_power(self, on: bool) -> None:
            calls.append(on)

    description = SWITCH_TYPES[0].description
    assert description.key == "power"
    _run(description.turn_on_fn(_FakeClient()))
    assert calls == [True]


def test_power_switch_turn_off_calls_set_power_false() -> None:
    calls: list[bool] = []

    class _FakeClient:
        async def set_power(self, on: bool) -> None:
            calls.append(on)

    description = SWITCH_TYPES[0].description
    _run(description.turn_off_fn(_FakeClient()))
    assert calls == [False]
