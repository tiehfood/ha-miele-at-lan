"""One appliance failing its first refresh must not take the others down.

Seen with a flaky dryer: it was enrolled, then answered `GET /State` with
404 during the first refresh. That raised out of `_setup_cloud`, the whole
entry became "not ready", and every appliance stayed unavailable until Home
Assistant's retry, which the dryer could fail again.
"""

import asyncio
import sys
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import custom_components.miele_lan as miele_lan  # noqa: E402
from custom_components.miele_lan.enrollment import (  # noqa: E402
    EnrolledDevice,
    EnrollmentResult,
)
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402

GOOD = "000100000001"
FLAKY = "000100000002"


def _enrolled(fab: str, ip: str) -> EnrolledDevice:
    return EnrolledDevice(
        fab=fab, host_ip=ip, route=fab, supervision_ok=False,
        subscriptions_ok=["/State/"], enrolled_at=0.0,
    )


class _Listener:
    hostname = "Miele-fake.local."

    def __init__(self, **kwargs) -> None:
        pass

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _Client:
    closed: list[str] = []

    def __init__(self, host: str) -> None:
        self.host = host

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        _Client.closed.append(self.host)


class _Coordinator:
    def __init__(self, hass, entry, client, *, fab, enrollment) -> None:
        self.fab = fab

    async def async_config_entry_first_refresh(self) -> None:
        if self.fab == FLAKY:
            raise ConfigEntryNotReady("ResponseError: HTTP error 404: API error for /State")

    async def async_ensure_ident_ready(self) -> None:
        return None


class _Watcher:
    created: list[set[str]] = []

    def __init__(self, *, missing_fabs, enrolled_ips, **kwargs) -> None:
        _Watcher.created.append(set(missing_fabs))
        self.enrolled_ips = enrolled_ips

    async def async_start(self) -> None:
        return None


class _ConfigEntries:
    async def async_forward_entry_setups(self, entry, platforms) -> None:
        return None


class _Hass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.config_entries = _ConfigEntries()


class _Entry:
    entry_id = "entry1"
    data = {
        "group_id": "0123456789ABCDEF",
        "group_key": "00" * 64,
        "devices": [{"fabNr": GOOD}, {"fabNr": FLAKY}],
        "ha_fab": "000999000001",
    }
    options = MappingProxyType({"advertise_address": "203.0.113.226"})

    def add_update_listener(self, cb):
        return lambda: None

    def async_create_background_task(self, hass, coro, name):
        return asyncio.get_running_loop().create_task(coro)


def test_flaky_first_refresh_leaves_only_that_appliance_out(monkeypatch) -> None:
    issues: list[dict] = []

    async def _zc(hass):
        return object()

    async def _discover(**kwargs):
        return []

    async def _enroll_all(*args, **kwargs):
        return EnrollmentResult(
            enrolled=[_enrolled(GOOD, "203.0.113.1"), _enrolled(FLAKY, "203.0.113.2")],
            failed=[],
        )

    async def _cleanup(**kwargs):
        return 0

    monkeypatch.setattr(miele_lan.ha_zeroconf, "async_get_async_instance", _zc)
    monkeypatch.setattr(miele_lan, "mdns_discover_household", _discover)
    monkeypatch.setattr(miele_lan, "enroll_all", _enroll_all)
    monkeypatch.setattr(miele_lan, "cleanup_stale_subscriptions", _cleanup)
    monkeypatch.setattr(miele_lan, "MielePushListener", _Listener)
    monkeypatch.setattr(miele_lan, "MieleLanCoordinator", _Coordinator)
    monkeypatch.setattr(miele_lan, "MissedApplianceWatcher", _Watcher)
    monkeypatch.setattr(miele_lan, "async_get_clientsession", lambda hass: None)
    monkeypatch.setattr(
        miele_lan.MieleLanClient, "from_hex",
        classmethod(lambda cls, *, host, **kwargs: _Client(host)),
    )
    monkeypatch.setattr(
        miele_lan.ir, "async_create_issue",
        lambda hass, domain, issue_id, **kwargs: issues.append(kwargs),
    )
    monkeypatch.setattr(miele_lan.ir, "async_delete_issue", lambda *a, **k: None)
    _Client.closed.clear()
    _Watcher.created.clear()

    hass = _Hass()

    async def _main():
        ok = await miele_lan._setup_cloud(hass, _Entry())
        await asyncio.sleep(0)  # let the background cleanup run
        return ok

    assert asyncio.run(_main()) is True

    bundle = hass.data[miele_lan.DOMAIN]["entry1"]
    assert list(bundle["coordinators"]) == [GOOD]
    assert [c.host for c in bundle["clients"]] == ["203.0.113.1"]
    assert _Client.closed == ["203.0.113.2"]
    assert _Watcher.created == [{FLAKY}]
    assert len(issues) == 1
    assert FLAKY in issues[0]["translation_placeholders"]["devices"]
    assert "first refresh failed" in issues[0]["translation_placeholders"]["devices"]
