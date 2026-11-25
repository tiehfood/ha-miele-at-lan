"""`_signed_get_fabs` must survive an appliance that is slow to wake from standby.

A single short attempt used to turn "key is fine, appliance asleep" into
"no devices reported", with the real reason hidden at DEBUG level.
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.miele_lan import enrollment as enrollment_mod  # noqa: E402


def _fake_client(outcomes: list):
    """MieleLanClient stand-in; each construction consumes one outcome."""
    calls: list[float] = []

    class _Client:
        def __init__(self, outcome):
            self._outcome = outcome

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_devices(self):
            if isinstance(self._outcome, Exception):
                raise self._outcome
            return self._outcome

        @classmethod
        def from_hex(cls, ip, gid, gkey, fab, timeout=None):
            calls.append(timeout)
            return cls(outcomes.pop(0))

    return _Client, calls


def _run(coro):
    return asyncio.run(coro)


def test_retries_after_a_timeout(monkeypatch):
    client, calls = _fake_client([asyncio.TimeoutError(), {"000111": {}}])
    monkeypatch.setattr(enrollment_mod, "MieleLanClient", client)
    fabs = _run(enrollment_mod._signed_get_fabs(ip="10.0.0.5", group_id_hex="A", group_key_hex="B"))
    assert fabs == ["000111"]
    assert len(calls) == 2
    assert all(t >= 10 for t in calls)  # generous timeout for a sleeping appliance


def test_success_needs_one_attempt(monkeypatch):
    client, calls = _fake_client([{"000222": {}}])
    monkeypatch.setattr(enrollment_mod, "MieleLanClient", client)
    fabs = _run(enrollment_mod._signed_get_fabs(ip="10.0.0.5", group_id_hex="A", group_key_hex="B"))
    assert fabs == ["000222"] and len(calls) == 1


def test_failure_is_logged_with_the_reason(monkeypatch, caplog):
    client, calls = _fake_client([OSError("no route"), OSError("no route")])
    monkeypatch.setattr(enrollment_mod, "MieleLanClient", client)
    with caplog.at_level(logging.WARNING, logger=enrollment_mod.__name__):
        fabs = _run(enrollment_mod._signed_get_fabs(ip="10.0.0.5", group_id_hex="A", group_key_hex="B"))
    assert fabs == [] and len(calls) == 2
    assert any("OSError" in r.getMessage() and "no route" in r.getMessage() for r in caplog.records)
