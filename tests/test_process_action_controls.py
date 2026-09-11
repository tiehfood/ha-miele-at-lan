"""Tests for Start/Stop/Pause/Resume via PUT /State (issue #10, #16).

No HA runtime, no network — same stub pattern as
tests/test_write_user_request_errors.py. Covers both the wire payload
(correct ProcessAction value, correct resource) and the precondition
logic, which must be usable without a device or a coordinator.
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asyncmiele.exceptions.network import ResponseError  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.miele_lan.api import (  # noqa: E402
    MieleLanClient,
    check_process_action_precondition,
)
from custom_components.miele_lan.const import (  # noqa: E402
    PROCESS_ACTION_PAUSE,
    PROCESS_ACTION_RESUME,
    PROCESS_ACTION_START,
    PROCESS_ACTION_STOP,
)


class _RecordingRawClient:
    """Stub replacing the asyncmiele MieleClient underneath MieleLanClient."""

    def __init__(self, *, raise_status: int | None = None) -> None:
        self.calls: list[tuple[str, str, object]] = []
        self._raise_status = raise_status

    async def _request_bytes(self, method, resource, *, body=None, allowed_status=(200,)):
        self.calls.append((method, resource, body))
        if self._raise_status is not None:
            raise ResponseError(self._raise_status, "stub failure")
        return 200, b""


def _run(coro):
    return asyncio.run(coro)


# --- wire payload ------------------------------------------------------------

@pytest.mark.parametrize(
    "method_name,expected_action",
    [
        ("start_process", PROCESS_ACTION_START),
        ("stop_process", PROCESS_ACTION_STOP),
        ("pause_process", PROCESS_ACTION_PAUSE),
        ("resume_process", PROCESS_ACTION_RESUME),
    ],
)
def test_process_action_sends_correct_put_state(method_name: str, expected_action: int) -> None:
    stub = _RecordingRawClient()
    client = MieleLanClient(stub, route="000000000000")
    _run(getattr(client, method_name)())
    assert len(stub.calls) == 1
    method, resource, body = stub.calls[0]
    assert method == "PUT"
    assert resource == "/Devices/000000000000/State"
    assert body == {"ProcessAction": expected_action}


def test_refusal_is_not_swallowed_as_success() -> None:
    """A 400 must surface as a refusal here, unlike `_put_state`'s no-op

    convention for idempotent fields (Light, DeviceAction) — treating a
    refused Start/Stop as silent success is the exact misleading-error
    pattern issue #16 already cost a reporter an afternoon on.
    """
    stub = _RecordingRawClient(raise_status=400)
    client = MieleLanClient(stub, route="000000000000")
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(client.start_process())
    assert "400" in str(exc_info.value)


def test_500_does_not_assert_a_specific_cause() -> None:
    stub = _RecordingRawClient(raise_status=500)
    client = MieleLanClient(stub, route="000000000000")
    with pytest.raises(HomeAssistantError) as exc_info:
        _run(client.stop_process())
    message = str(exc_info.value)
    assert "500" in message
    assert "Remote control" not in message
    assert "panel" not in message.lower()


# --- precondition (pure, no I/O) ---------------------------------------------

def test_precondition_none_when_state_unknown() -> None:
    assert check_process_action_precondition({}, PROCESS_ACTION_START) is None


def test_precondition_blocks_when_remote_control_disabled() -> None:
    state = {"RemoteEnable": [0, 0, 0], "Status": 4}
    message = check_process_action_precondition(state, PROCESS_ACTION_STOP)
    assert message is not None
    assert "RemoteEnable" in message
    assert "[0, 0, 0]" in message


def test_precondition_blocks_start_when_not_waiting_to_start() -> None:
    state = {"RemoteEnable": [15, 1, 1], "Status": 5}
    message = check_process_action_precondition(state, PROCESS_ACTION_START)
    assert message is not None
    assert "Status=5" in message


def test_precondition_does_not_apply_status_gate_to_stop() -> None:
    """Status==4 ("waiting to start") is only documented as a Start gate."""
    state = {"RemoteEnable": [15, 1, 1], "Status": 5}
    assert check_process_action_precondition(state, PROCESS_ACTION_STOP) is None


def test_precondition_passes_when_ready_to_start() -> None:
    state = {"RemoteEnable": [15, 1, 1], "Status": 4}
    assert check_process_action_precondition(state, PROCESS_ACTION_START) is None


# --- client wiring of the precondition ---------------------------------------

def test_client_blocks_locally_without_a_network_call() -> None:
    stub = _RecordingRawClient()
    client = MieleLanClient(stub, route="000000000000")
    state = {"RemoteEnable": [0, 0, 0], "Status": 4}
    with pytest.raises(HomeAssistantError):
        _run(client.start_process(state))
    assert stub.calls == []


def test_client_proceeds_when_precondition_state_is_missing() -> None:
    stub = _RecordingRawClient()
    client = MieleLanClient(stub, route="000000000000")
    _run(client.start_process(None))
    assert len(stub.calls) == 1
