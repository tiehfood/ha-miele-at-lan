"""Tests for the Dop1 hood write/read methods on MieleLanClient.

No HA, no network — the underlying client is a stub that records the request
it was handed and can be told to fail with a given HTTP status.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from asyncmiele.exceptions.network import ResponseError  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.miele_lan.api import MieleLanClient  # noqa: E402
from custom_components.miele_lan.const import (  # noqa: E402
    PF_DA_FETTFILTER_GRENZE_AKTUELL,
)

FAB = "000000000000"


class _StubRawClient:
    """Records calls; optionally raises ResponseError or returns a body."""

    def __init__(self, *, status_code: int | None = None, body: bytes = b"") -> None:
        self._status_code = status_code
        self._body = body
        self.calls: list[tuple[str, str, dict | None]] = []

    async def _request_bytes(self, method, resource, *, body=None, allowed_status=(200,)):
        self.calls.append((method, resource, body))
        if self._status_code is not None:
            raise ResponseError(self._status_code, "stub failure")
        return 200, self._body


def _client(**kwargs) -> tuple[MieleLanClient, _StubRawClient]:
    raw = _StubRawClient(**kwargs)
    return MieleLanClient(raw, route=FAB), raw


# --------------------------------------------------------------- transport
def test_fan_level_posts_to_the_dop1_endpoint() -> None:
    client, raw = _client()
    asyncio.run(client.set_fan_level(2))
    method, resource, body = raw.calls[0]
    assert method == "POST"
    assert resource == f"/Devices/{FAB}/DOP/"
    assert body == {"Request": "8E01020000FF0200"}


def test_run_on_time_and_main_light_use_the_same_endpoint() -> None:
    client, raw = _client()
    asyncio.run(client.set_fan_run_on_time(15))
    asyncio.run(client.set_main_light_dop1(True))
    assert {resource for _, resource, _ in raw.calls} == {f"/Devices/{FAB}/DOP/"}
    assert raw.calls[0][2] == {"Request": "8E060200000F"}
    assert raw.calls[1][2]["Request"].startswith("14000200")


def test_write_setting_pf_sends_the_write_request_type() -> None:
    client, raw = _client()
    asyncio.run(client.write_setting_pf(PF_DA_FETTFILTER_GRENZE_AKTUELL, 1))
    assert raw.calls[0][2] == {"Request": "12010200009C4A00000001"}


# ------------------------------------------------------- error conventions
def test_http_400_is_treated_as_a_benign_no_op() -> None:
    """Re-asserting the current value must not surface as an error."""
    client, _ = _client(status_code=400)
    asyncio.run(client.set_fan_level(1))  # no raise
    asyncio.run(client.set_main_light_dop1(False))  # no raise
    assert asyncio.run(client.read_setting_pf(PF_DA_FETTFILTER_GRENZE_AKTUELL)) is None


@pytest.mark.parametrize("status", [403, 404, 500])
def test_other_http_errors_surface_as_home_assistant_errors(status: int) -> None:
    client, _ = _client(status_code=status)
    with pytest.raises(HomeAssistantError) as exc_info:
        asyncio.run(client.set_fan_level(1))
    message = str(exc_info.value)
    assert "Fan level write" in message
    assert str(status) in message


def test_invalid_values_are_rejected_before_any_request_is_made() -> None:
    client, raw = _client()
    with pytest.raises(ValueError):
        asyncio.run(client.set_fan_level(9))
    with pytest.raises(ValueError):
        asyncio.run(client.set_fan_run_on_time(7))
    assert raw.calls == []


# ------------------------------------------------------------ setting reads
def test_read_setting_pf_extracts_the_value_from_the_json_response() -> None:
    pf = PF_DA_FETTFILTER_GRENZE_AKTUELL
    response_hex = f"1201010000{pf:04X}{3:08X}{0:08X}{4:08X}"
    body = json.dumps({"Response": response_hex}).encode()
    client, _ = _client(body=body)
    assert asyncio.run(client.read_setting_pf(pf)) == 3


def test_read_setting_pf_returns_none_for_an_empty_body() -> None:
    client, _ = _client(body=b"")
    assert asyncio.run(client.read_setting_pf(PF_DA_FETTFILTER_GRENZE_AKTUELL)) is None


def test_read_setting_pf_returns_none_when_the_appliance_answers_about_another_setting() -> None:
    other = PF_DA_FETTFILTER_GRENZE_AKTUELL + 1
    response_hex = f"1201010000{other:04X}{2:08X}{0:08X}{4:08X}"
    body = json.dumps({"Response": response_hex}).encode()
    client, _ = _client(body=body)
    assert asyncio.run(client.read_setting_pf(PF_DA_FETTFILTER_GRENZE_AKTUELL)) is None
