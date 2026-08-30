"""Pure-Python tests for write_user_request's HTTP-status -> error-message mapping.

No HA, no network — the underlying client is a stub that raises ResponseError.
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asyncmiele.exceptions.network import ResponseError  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.miele_lan.api import MieleLanClient  # noqa: E402
from custom_components.miele_lan.const import OPCODE_SWITCH_OFF  # noqa: E402


class _StubRawClient:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    async def _request_bytes(self, *args, **kwargs):
        raise ResponseError(self._status_code, "stub failure")


def _write_user_request(status_code: int) -> str:
    client = MieleLanClient(_StubRawClient(status_code), route="000000000000")
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(client.write_user_request(OPCODE_SWITCH_OFF))
    return str(exc_info.value)


def test_404_reports_firmware_block() -> None:
    message = _write_user_request(404)
    assert "does not accept remote commands" in message


def test_403_reports_remote_control_refused() -> None:
    message = _write_user_request(403)
    assert "Remote control was refused" in message


def test_500_does_not_blame_a_setting() -> None:
    message = _write_user_request(500)
    assert "Remote control was refused" not in message
    assert "Mobile controllable" not in message
    assert "500" in message


def test_other_status_reports_generic_failure() -> None:
    message = _write_user_request(409)
    assert "409" in message
