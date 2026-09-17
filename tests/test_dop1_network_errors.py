"""Pure-Python tests for _dop1_post's network-error -> readable-message mapping.

No HA, no network — the underlying client is a stub that raises NetworkTimeoutError,
NetworkConnectionError or ResponseError.
"""

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asyncmiele.exceptions.network import (  # noqa: E402
    NetworkConnectionError,
    NetworkTimeoutError,
    ResponseError,
)
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.miele_lan.api import MieleLanClient  # noqa: E402


class _RaisingRawClient:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def _request_bytes(self, *args, **kwargs):
        raise self._exc


class _StubRawClient:
    def __init__(self, status_code: int) -> None:
        self._status_code = status_code

    async def _request_bytes(self, *args, **kwargs):
        raise ResponseError(self._status_code, "stub failure")


@pytest.mark.parametrize(
    "caller",
    [
        lambda client: client.set_fan_level(2),
        lambda client: client.set_fan_run_on_time(5),
        lambda client: client.set_main_light_dop1(True),
        lambda client: client.write_setting_pf(1, 1),
        lambda client: client.read_setting_pf(1),
    ],
)
def test_network_timeout_reports_readable_error(caller) -> None:
    client = MieleLanClient(
        _RaisingRawClient(NetworkTimeoutError("timed out")), route="000000000000"
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(caller(client))
    assert str(exc_info.value) == "The appliance did not respond to the command (timeout)."


@pytest.mark.parametrize(
    "caller",
    [
        lambda client: client.set_fan_level(2),
        lambda client: client.set_fan_run_on_time(5),
        lambda client: client.set_main_light_dop1(True),
        lambda client: client.write_setting_pf(1, 1),
        lambda client: client.read_setting_pf(1),
    ],
)
def test_network_connection_error_reports_readable_error(caller) -> None:
    client = MieleLanClient(
        _RaisingRawClient(NetworkConnectionError("refused")), route="000000000000"
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(caller(client))
    assert str(exc_info.value) == "Could not connect to the appliance."


def test_400_is_still_treated_as_no_op_success() -> None:
    client = MieleLanClient(_StubRawClient(400), route="000000000000")
    asyncio.run(client.set_fan_level(2))


def test_non_400_response_error_still_reports_http_status() -> None:
    client = MieleLanClient(_StubRawClient(500), route="000000000000")
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(client.set_fan_level(2))
    assert "500" in str(exc_info.value)
