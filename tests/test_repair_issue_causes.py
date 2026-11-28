"""Which enrollment failures are transient: nothing for the user to fix.

A repair issue has to be something the user can act on (Home Assistant's
repair-issues rule). An appliance that did not answer, or answered that it
was busy, is not that while it is set up and merely unavailable.
"""
import asyncio

import aiohttp
import pytest
from asyncmiele.exceptions.api import DecryptionError
from asyncmiele.exceptions.network import (
    NetworkConnectionError,
    NetworkTimeoutError,
    ResponseError,
)

from custom_components.miele_lan import enrollment as enrollment_mod
from custom_components.miele_lan.enrollment import (
    DeviceIpResolver,
    enroll_all,
    is_transient_failure,
)


@pytest.mark.parametrize(
    "err",
    [
        NetworkTimeoutError("timed out"),
        NetworkConnectionError("refused"),
        TimeoutError(),
        aiohttp.ClientConnectionError(),
        ResponseError(503, "busy right after a reload"),
        ResponseError(500),
    ],
)
def test_no_answer_or_busy_is_transient(err) -> None:
    assert is_transient_failure(err)


@pytest.mark.parametrize(
    "err",
    [ResponseError(403), ResponseError(404), DecryptionError("bad key"), ValueError("parse")],
)
def test_an_answer_that_refuses_is_not_transient(err) -> None:
    assert not is_transient_failure(err)


def test_an_appliance_without_an_address_is_transient(monkeypatch) -> None:
    async def _no_refine(failed, **_kwargs):
        return failed

    monkeypatch.setattr(enrollment_mod, "_refine_failure_reasons", _no_refine)

    result = asyncio.run(enroll_all(
        [{"fabNr": "000100000001"}],
        group_id_hex="00" * 8,
        group_key_hex="00" * 64,
        ha_fab="000999000001",
        ha_hostname="ha.local.",
        ha_port=18082,
        resolver=DeviceIpResolver(static={}),
        session=None,  # type: ignore[arg-type]
    ))

    assert [f.transient for f in result.failed] == [True]
