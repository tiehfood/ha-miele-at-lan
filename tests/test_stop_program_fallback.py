"""Oven stop falls back to /State ProcessAction 2 when DOP2 writes are blocked.

An H6800BMX (XKM3100W, protocol 2) answers HTTP 404 to every DOP2 write. A
PUT /State {"ProcessAction": 2} on it was acknowledged with
[{"Success":{"ProcessAction":2}}] and the running programme ended (Status 5
to 2). A Wake (DeviceAction 2) on the same oven and on a G6992 dishwasher
answered HTTP 200 with [{"Failure":{"DeviceAction":null}}] and did nothing.
"""
import asyncio

import pytest
from asyncmiele.exceptions.network import ResponseError
from homeassistant.exceptions import HomeAssistantError

from custom_components.miele_lan.api import ActionRejectedError, DopWritesBlockedError, MieleLanClient
from custom_components.miele_lan.const import PROCESS_ACTION_STOP

FAB = "000000000000"
DOP2_RESOURCE = f"/Devices/{FAB}/DOP2/2/1583?idx1=0&idx2=0"
STATE_RESOURCE = f"/Devices/{FAB}/State"
SUCCESS = b'[{"Success":{"ProcessAction":2}}]'
FAILURE = b'[{"Failure":{"DeviceAction":null}}]'


class _QueuedRawClient:
    """Answers each request with the next queued item, raising exceptions."""

    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = []

    async def _request_bytes(self, method, resource, body=None, allowed_status=None):
        self.calls.append((method, resource, body))
        answer = self._answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _run(coro):
    return asyncio.run(coro)


def test_dop2_success_does_not_touch_state() -> None:
    stub = _QueuedRawClient([(200, b"")])
    _run(MieleLanClient(stub, route=FAB).stop_program())
    assert [c[1] for c in stub.calls] == [DOP2_RESOURCE]


def test_404_falls_back_to_process_action_stop() -> None:
    stub = _QueuedRawClient([ResponseError(404, "no dop2"), (200, SUCCESS)])
    _run(MieleLanClient(stub, route=FAB).stop_program())
    assert [c[1] for c in stub.calls] == [DOP2_RESOURCE, STATE_RESOURCE]
    assert stub.calls[1][0] == "PUT"
    assert stub.calls[1][2] == {"ProcessAction": PROCESS_ACTION_STOP}


@pytest.mark.parametrize("status,needle", [(403, "Remote control was refused"), (500, "500")])
def test_other_refusals_do_not_fall_back(status, needle) -> None:
    stub = _QueuedRawClient([ResponseError(status, "refused")])
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(MieleLanClient(stub, route=FAB).stop_program())
    assert needle in str(exc_info.value)
    assert [c[1] for c in stub.calls] == [DOP2_RESOURCE]


def test_fallback_refusal_is_reported() -> None:
    stub = _QueuedRawClient([ResponseError(404, "no dop2"), ResponseError(400, "bad")])
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(MieleLanClient(stub, route=FAB).stop_program())
    assert "400" in str(exc_info.value)


def test_fallback_failure_entry_is_reported() -> None:
    stub = _QueuedRawClient([ResponseError(404, "no dop2"), (200, FAILURE)])
    with pytest.raises(ActionRejectedError) as exc_info:
        _run(MieleLanClient(stub, route=FAB).stop_program())
    assert "does not support this action" in str(exc_info.value)


def test_404_is_a_distinct_error_type() -> None:
    stub = _QueuedRawClient([ResponseError(404, "no dop2")])
    with pytest.raises(DopWritesBlockedError):
        _run(MieleLanClient(stub, route=FAB).write_user_request(0x13))


# --- /State writes that the appliance acknowledges with a Failure entry -----

def test_wake_failure_response_is_an_error_not_a_success() -> None:
    stub = _QueuedRawClient([(200, FAILURE)])
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(MieleLanClient(stub, route=FAB).wake())
    message = str(exc_info.value)
    assert "does not support this action" in message
    assert "DeviceAction" in message


def test_wake_success_response_is_returned() -> None:
    stub = _QueuedRawClient([(200, b'[{"Success":{"DeviceAction":0}}]')])
    result = _run(MieleLanClient(stub, route=FAB).wake())
    assert result == {"Success": {"DeviceAction": 0}}


def test_light_failure_response_is_an_error() -> None:
    stub = _QueuedRawClient([(200, b'[{"Failure":{"Light":null}}]')])
    with pytest.raises(HomeAssistantError):
        _run(MieleLanClient(stub, route=FAB).light_on())


@pytest.mark.parametrize("raw", [b"", b"not json", b"[]", b'[{"Success":{"Light":2}}]'])
def test_responses_without_a_failure_entry_are_accepted(raw) -> None:
    stub = _QueuedRawClient([(200, raw)])
    _run(MieleLanClient(stub, route=FAB).wake())
