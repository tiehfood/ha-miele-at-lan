"""aiohttp errors in the patched MieleClient._request_bytes. No network.

A stub aiohttp session raises what a dropped keep-alive or a reset socket
raises; callers must see NetworkConnectionError, which they already map.
"""
import asyncio

import aiohttp
import pytest
from asyncmiele.exceptions.network import NetworkConnectionError
from homeassistant.exceptions import HomeAssistantError

from custom_components.miele_lan.api import MieleLanClient

ROUTE = "000000000000"


class _FailingSession:
    closed = False

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def request(self, *args, **kwargs):
        raise self._exc


def _client(exc: Exception) -> MieleLanClient:
    client = MieleLanClient.from_hex("127.0.0.1", "0123456789ABCDEF", "11" * 64, ROUTE)
    client.raw._session = _FailingSession(exc)
    return client


@pytest.mark.parametrize(
    "exc",
    [aiohttp.ServerDisconnectedError(), aiohttp.ClientOSError(54, "Connection reset by peer")],
)
def test_client_errors_become_connection_errors(exc: Exception) -> None:
    client = _client(exc)
    with pytest.raises(NetworkConnectionError):
        asyncio.run(client.raw._request_bytes("GET", f"/Devices/{ROUTE}/State"))


def test_write_reports_a_readable_error_on_a_dropped_connection() -> None:
    client = _client(aiohttp.ServerDisconnectedError())
    with pytest.raises(HomeAssistantError, match="Could not connect to the appliance"):
        asyncio.run(client.light_on())
