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


DELETED_ISSUES: list[str] = []


def _patch_setup(monkeypatch, *, failed=(), watcher=_Watcher) -> list[dict]:
    """Patch everything `_setup_cloud` touches on the network: GOOD and FLAKY
    enrol (FLAKY then fails its first refresh), `failed` could not be
    enrolled. Returns the repair issues raised; DELETED_ISSUES collects the
    ids of the ones deleted."""
    issues: list[dict] = []
    DELETED_ISSUES.clear()

    async def _zc(hass):
        return object()

    async def _discover(**kwargs):
        return []

    async def _enroll_all(*args, **kwargs):
        return EnrollmentResult(
            enrolled=[_enrolled(GOOD, "203.0.113.1"), _enrolled(FLAKY, "203.0.113.2")],
            failed=list(failed),
        )

    async def _cleanup(**kwargs):
        return 0

    monkeypatch.setattr(miele_lan.ha_zeroconf, "async_get_async_instance", _zc)
    monkeypatch.setattr(miele_lan, "mdns_discover_household", _discover)
    monkeypatch.setattr(miele_lan, "enroll_all", _enroll_all)
    monkeypatch.setattr(miele_lan, "cleanup_stale_subscriptions", _cleanup)
    monkeypatch.setattr(miele_lan, "MielePushListener", _Listener)
    monkeypatch.setattr(miele_lan, "MieleLanCoordinator", _Coordinator)
    monkeypatch.setattr(miele_lan, "MissedApplianceWatcher", watcher)
    monkeypatch.setattr(miele_lan, "async_get_clientsession", lambda hass: None)
    monkeypatch.setattr(
        miele_lan.MieleLanClient, "from_hex",
        classmethod(lambda cls, *, host, **kwargs: _Client(host)),
    )
    monkeypatch.setattr(
        miele_lan.ir, "async_create_issue",
        lambda hass, domain, issue_id, **kwargs: issues.append({"issue_id": issue_id, **kwargs}),
    )
    monkeypatch.setattr(
        miele_lan.ir, "async_delete_issue",
        lambda hass, domain, issue_id: DELETED_ISSUES.append(issue_id),
    )
    monkeypatch.setattr(miele_lan, "registered_appliance_name", lambda hass, fab: None)
    _Client.closed.clear()
    _Watcher.created.clear()
    return issues


def test_flaky_first_refresh_leaves_only_that_appliance_out(monkeypatch) -> None:
    issues = _patch_setup(monkeypatch)
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
    assert [i["issue_id"] for i in issues] == [f"unenrolled_entry1_{FLAKY}"]
    assert issues[0]["translation_placeholders"]["appliance"] == FLAKY
    assert "first refresh failed" in issues[0]["translation_placeholders"]["reason"]


def test_setup_tells_the_recovery_backoff_what_it_missed(monkeypatch) -> None:
    """The recovery reload was for FLAKY, and setup missed it again: the gap
    before the next recovery reload has to grow."""
    from custom_components.miele_lan.recovery import (
        DATA_RECOVERY_RELOADS,
        RecoveryReloads,
    )

    _patch_setup(monkeypatch)
    hass = _Hass()
    hass.data[DATA_RECOVERY_RELOADS] = {
        "entry1": RecoveryReloads(last=0.0, gap=300.0, pending=frozenset({FLAKY})),
    }

    assert asyncio.run(miele_lan._setup_cloud(hass, _Entry())) is True

    assert hass.data[DATA_RECOVERY_RELOADS]["entry1"].gap == 600.0


def test_watcher_gets_the_known_addresses_of_missed_appliances(monkeypatch) -> None:
    """Where mDNS does not reach Home Assistant, addresses are configured by
    hand and no announcement will come; the watcher has to probe those."""
    from custom_components.miele_lan.const import CONF_STATIC_IPS
    from custom_components.miele_lan.enrollment_report import FailedDevice

    off = "000100000003"
    seen: list[dict] = []

    class _RecordingWatcher(_Watcher):
        def __init__(self, *, known_addresses, **kwargs) -> None:
            super().__init__(**kwargs)
            seen.append(known_addresses)

    class _StaticEntry(_Entry):
        data = {**_Entry.data, "devices": [{"fabNr": GOOD}, {"fabNr": FLAKY}, {"fabNr": off}]}
        options = MappingProxyType({
            "advertise_address": "203.0.113.226",
            CONF_STATIC_IPS: {off: "203.0.113.3"},
        })

    _patch_setup(
        monkeypatch,
        failed=[FailedDevice(fab=off, reason="signed verify failed")],
        watcher=_RecordingWatcher,
    )

    assert asyncio.run(miele_lan._setup_cloud(_Hass(), _StaticEntry())) is True

    # The configured address of the appliance that could not be enrolled, and
    # the enrolled address of the one whose first refresh failed. Not the
    # working appliance's.
    assert seen == [{off: "203.0.113.3", FLAKY: "203.0.113.2"}]


def test_issue_names_appliances_like_home_assistant_does(monkeypatch) -> None:
    """The user's own device name first, then the type from the cloud list."""
    from custom_components.miele_lan.enrollment_report import FailedDevice

    off = "000100000003"

    class _TypedEntry(_Entry):
        data = {
            **_Entry.data,
            "devices": [
                {"fabNr": GOOD, "deviceType": 1},
                {"fabNr": FLAKY, "deviceType": 2},
                {"fabNr": off, "deviceType": 7},
            ],
        }

    issues = _patch_setup(
        monkeypatch, failed=[FailedDevice(fab=off, reason="no LAN IP found")]
    )
    monkeypatch.setattr(
        miele_lan, "registered_appliance_name",
        lambda hass, fab: "Downstairs dryer" if fab == FLAKY else None,
    )

    assert asyncio.run(miele_lan._setup_cloud(_Hass(), _TypedEntry())) is True

    placeholders = {i["issue_id"]: i["translation_placeholders"] for i in issues}
    assert placeholders[f"unenrolled_entry1_{off}"]["appliance"] == f"Dishwasher ({off})"
    assert placeholders[f"unenrolled_entry1_{FLAKY}"]["name"] == "Downstairs dryer"
    assert placeholders[f"unenrolled_entry1_{FLAKY}"]["appliance"] == (
        f"Downstairs dryer ({FLAKY})"
    )


def test_only_an_appliance_that_set_up_has_its_issue_deleted(monkeypatch) -> None:
    """Deleting an issue and creating it again shows it as a new one and
    forgets that the user ignored it, so a missing appliance's issue is left
    alone. The issue that used to list every appliance goes away."""
    issues = _patch_setup(monkeypatch)

    assert asyncio.run(miele_lan._setup_cloud(_Hass(), _Entry())) is True

    assert sorted(DELETED_ISSUES) == ["unenrolled_devices_entry1", f"unenrolled_entry1_{GOOD}"]
    assert [i["issue_id"] for i in issues] == [f"unenrolled_entry1_{FLAKY}"]


def test_registered_appliance_name_prefers_the_users_name(monkeypatch) -> None:
    from types import SimpleNamespace

    devices = {
        GOOD: SimpleNamespace(name="Washing Machine", name_by_user="Basement washer"),
        FLAKY: SimpleNamespace(name="Tumble Dryer", name_by_user=None),
    }

    class _Registry:
        def async_get_device(self, *, identifiers):
            ((domain, fab),) = identifiers
            assert domain == miele_lan.DOMAIN
            return devices.get(fab)

    monkeypatch.setattr(miele_lan.dr, "async_get", lambda hass: _Registry())

    assert miele_lan.registered_appliance_name(None, GOOD) == "Basement washer"
    assert miele_lan.registered_appliance_name(None, FLAKY) == "Tumble Dryer"
    assert miele_lan.registered_appliance_name(None, "000100000009") is None
