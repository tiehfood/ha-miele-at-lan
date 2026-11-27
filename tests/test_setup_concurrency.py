"""Setup must not wait for appliances one after another.

Drives `_setup_cloud` with fake listener, clients and coordinators, so these
tests pin how setup schedules the per-appliance work, not what that work does.
"""

import asyncio
import sys
import time
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import custom_components.miele_lan as miele_lan  # noqa: E402
from custom_components.miele_lan.enrollment import (  # noqa: E402
    EnrolledDevice,
    EnrollmentResult,
)
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402

WASHER = "000100000001"
DRYER = "000100000002"
SLOW = 0.3


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
    def __init__(self, host: str, events: list[str]) -> None:
        self.host = host
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self._events.append(f"closed {self.host}")


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
        "devices": [{"fabNr": WASHER}, {"fabNr": DRYER}],
        "ha_fab": "000999000001",
    }
    options = MappingProxyType({"advertise_address": "203.0.113.226"})

    def __init__(self) -> None:
        self.background: list[asyncio.Task] = []

    def add_update_listener(self, cb):
        return lambda: None

    def async_create_background_task(self, hass, coro, name):
        task = asyncio.get_running_loop().create_task(coro)
        self.background.append(task)
        return task


def _patch_setup(monkeypatch, *, refresh, cleanup=None) -> list[str]:
    """Patch everything `_setup_cloud` touches on the network. `refresh(fab)`
    is each coordinator's first refresh. Returns the event log the fakes
    append to."""
    events: list[str] = []

    async def _zc(hass):
        return object()

    async def _discover(**kwargs):
        return []

    async def _enroll_all(*args, **kwargs):
        return EnrollmentResult(
            enrolled=[_enrolled(WASHER, "203.0.113.1"), _enrolled(DRYER, "203.0.113.2")],
            failed=[],
        )

    async def _no_cleanup(**kwargs):
        return 0

    class _Coordinator:
        def __init__(self, hass, entry, client, *, fab, enrollment) -> None:
            self.fab = fab

        async def async_config_entry_first_refresh(self) -> None:
            await refresh(self.fab)
            events.append(f"refreshed {self.fab}")

        async def async_ensure_ident_ready(self) -> None:
            return None

    monkeypatch.setattr(miele_lan.ha_zeroconf, "async_get_async_instance", _zc)
    monkeypatch.setattr(miele_lan, "mdns_discover_household", _discover)
    monkeypatch.setattr(miele_lan, "enroll_all", _enroll_all)
    monkeypatch.setattr(miele_lan, "cleanup_stale_subscriptions", cleanup or _no_cleanup)
    monkeypatch.setattr(miele_lan, "MielePushListener", _Listener)
    monkeypatch.setattr(miele_lan, "MieleLanCoordinator", _Coordinator)
    monkeypatch.setattr(miele_lan, "async_get_clientsession", lambda hass: None)
    monkeypatch.setattr(
        miele_lan.MieleLanClient, "from_hex",
        classmethod(lambda cls, *, host, **kwargs: _Client(host, events)),
    )
    monkeypatch.setattr(miele_lan.ir, "async_create_issue", lambda *a, **k: None)
    monkeypatch.setattr(miele_lan.ir, "async_delete_issue", lambda *a, **k: None)
    return events


def test_first_refresh_waits_for_the_slowest_appliance_not_the_sum(monkeypatch) -> None:
    async def _refresh(fab: str) -> None:
        # The washer finishes last, so the order check below is meaningful.
        await asyncio.sleep(SLOW if fab == WASHER else SLOW * 0.8)

    events = _patch_setup(monkeypatch, refresh=_refresh)
    hass = _Hass()

    start = time.monotonic()
    assert asyncio.run(miele_lan._setup_cloud(hass, _Entry())) is True
    elapsed = time.monotonic() - start

    assert elapsed < SLOW * 1.4, f"took {elapsed:.2f}s, refreshes ran one after another"
    assert events == [f"refreshed {DRYER}", f"refreshed {WASHER}"]
    bundle = hass.data[miele_lan.DOMAIN]["entry1"]
    # Enrollment order, not the order the refreshes finished in.
    assert list(bundle["coordinators"]) == [WASHER, DRYER]


def test_a_failed_first_refresh_lets_the_others_finish_before_closing_clients(
    monkeypatch,
) -> None:
    async def _refresh(fab: str) -> None:
        if fab == WASHER:
            raise ConfigEntryNotReady("HTTP error 404: API error for /State")
        await asyncio.sleep(SLOW)

    events = _patch_setup(monkeypatch, refresh=_refresh)

    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(miele_lan._setup_cloud(_Hass(), _Entry()))

    # The dryer's refresh was still using its client: it must finish before
    # setup tears the clients down.
    assert events == [
        f"refreshed {DRYER}", "closed 203.0.113.1", "closed 203.0.113.2",
    ]
