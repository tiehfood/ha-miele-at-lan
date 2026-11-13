"""Response unpadding vs DOP2 data. No network.

The appliance pads encrypted bodies with 0x20, which is also a valid last
byte of a binary DOP2 leaf. Bodies are encrypted with the push listener's
own MieleH256 signer and served by a stub aiohttp session.
"""
import asyncio
import json
import re
from pathlib import Path

import pytest

from custom_components.miele_lan.api import MieleLanClient
from custom_components.miele_lan.push_listener import _sign_and_encrypt

GROUP_ID = "0123456789ABCDEF"
GROUP_KEY = "11" * 64
ROUTE = "000000000000"
FIXTURES = Path(__file__).parent / "fixtures"


class _Response:
    def __init__(self, body: bytes, signature: str) -> None:
        self.status = 200
        self.headers = {"X-Signature": signature}
        self._body = body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args) -> bool:
        return False


class _EncryptingSession:
    closed = False

    def __init__(self, plain: bytes) -> None:
        self._plain = plain

    def request(self, *args, **kwargs):
        return _Response(*_sign_and_encrypt(
            status=200, content_type="application/vnd.miele.v1+json; charset=utf-8",
            date="x", body_bytes=self._plain, group_id=GROUP_ID, group_key=GROUP_KEY,
        ))


def _get(plain: bytes, resource: str) -> bytes:
    client = MieleLanClient.from_hex("127.0.0.1", GROUP_ID, GROUP_KEY, ROUTE)
    client.raw._session = _EncryptingSession(plain)
    return asyncio.run(client.raw._request_bytes("GET", resource))[1]


def _fixture(name: str) -> bytes:
    return bytes.fromhex(re.sub(r"[^0-9a-fA-F]", "", (FIXTURES / name).read_text()))


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*_dop2_*.hex")))
def test_dop2_leaf_ending_in_0x20_is_not_truncated(name: str) -> None:
    leaf = _fixture(name)[:-1] + b"\x20"
    assert _get(leaf, f"/Devices/{ROUTE}/DOP2/2/1585?idx1=0&idx2=0") == leaf


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*_dop2_*.hex")))
def test_dop2_leaf_round_trips(name: str) -> None:
    leaf = _fixture(name)
    assert _get(leaf, f"/Devices/{ROUTE}/DOP2/2/1585?idx1=0&idx2=0") == leaf


def test_json_body_still_parses() -> None:
    body = _get(b'{"Status":5}', f"/Devices/{ROUTE}/State")
    assert json.loads(body) == {"Status": 5}
    assert not body.endswith(b" ")


def test_dop2_json_error_body_still_parses() -> None:
    body = _get(b'{"Error":20660}', f"/Devices/{ROUTE}/DOP2/2/137?idx1=0&idx2=0")
    assert json.loads(body) == {"Error": 20660}
