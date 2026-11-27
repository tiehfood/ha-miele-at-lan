"""Semi-professional appliances advertise `_mielesemiprof._tcp`, not `_mieleathome._tcp`.

Observed on a PG8133 dishwasher: same TXT records (`group=`, `devicetype=8`)
and the same signed REST API, only the mDNS service type differs. Every
mDNS browse must look under both types, or such an appliance is listed by
the cloud but never gets a LAN IP ("mDNS did not answer").

The fake browser below only reports instances whose service type was
actually asked for, the way a real mDNS browse behaves.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import enrollment as enrollment_mod  # noqa: E402
from custom_components.miele_lan.const import MIELE_SERVICE_TYPES  # noqa: E402
from custom_components.miele_lan.enrollment import (  # noqa: E402
    _mdns_groups_by_hostname,
    mdns_discover_household,
    mdns_household_ids,
)

GROUP = "0123456789ABCDEF"
HOME = "_mieleathome._tcp.local."
SEMIPROF = "_mielesemiprof._tcp.local."
WASHER = f"Miele WWV980.{HOME}"
DISHWASHER = f"Miele PG8133.{SEMIPROF}"


class _Info:
    def __init__(self, *, ip: str, devicetype: int, server: str) -> None:
        self.properties = {b"group": GROUP.encode(), b"devicetype": str(devicetype).encode()}
        self.server = server
        self._ip = ip

    def parsed_addresses(self) -> list[str]:
        return [self._ip]


INFOS = {
    WASHER: _Info(ip="203.0.113.116", devicetype=1, server="Miele-001D63FFFE0A0A01.local."),
    DISHWASHER: _Info(ip="203.0.113.232", devicetype=8, server="Miele-001D63FFFE0A0A02.local."),
}


class _FakeZeroconf:
    def __init__(self) -> None:
        self.zeroconf = object()

    async def async_get_service_info(self, service_type, name, timeout):
        return INFOS[name] if name.endswith(f".{service_type}") else None

    async def async_close(self) -> None:
        return None


def _patch_browser(monkeypatch) -> list[list[str]]:
    from zeroconf import ServiceStateChange

    browsed: list[list[str]] = []

    class _Browser:
        def __init__(self, zc, service_types, handlers) -> None:
            browsed.append(list(service_types))
            for name in INFOS:
                for st in service_types:
                    if name.endswith(f".{st}"):
                        handlers[0](None, st, name, ServiceStateChange.Added)

        async def async_cancel(self) -> None:
            return None

    monkeypatch.setattr("zeroconf.asyncio.AsyncServiceBrowser", _Browser)
    return browsed


def test_service_types_cover_household_and_semiprofessional() -> None:
    assert HOME in MIELE_SERVICE_TYPES
    assert SEMIPROF in MIELE_SERVICE_TYPES


def test_manifest_zeroconf_matches_service_types() -> None:
    manifest = json.loads(
        (ROOT / "custom_components" / "miele_lan" / "manifest.json").read_text()
    )
    assert [z["type"] for z in manifest["zeroconf"]] == list(MIELE_SERVICE_TYPES)


def test_discover_household_finds_semiprofessional_dishwasher(monkeypatch) -> None:
    browsed = _patch_browser(monkeypatch)
    fabs_by_ip = {"203.0.113.116": ["000100000001"], "203.0.113.232": ["000100000002"]}

    async def _fake_signed_get_fabs(*, ip, group_id_hex, group_key_hex):
        return fabs_by_ip[ip]

    monkeypatch.setattr(enrollment_mod, "_signed_get_fabs", _fake_signed_get_fabs)

    results = asyncio.run(mdns_discover_household(
        GROUP, "00" * 64, timeout=0, zeroconf=_FakeZeroconf(),
    ))

    assert browsed == [list(MIELE_SERVICE_TYPES)]
    by_fab = {r["fabNr"]: r for r in results}
    assert set(by_fab) == {"000100000001", "000100000002"}
    assert by_fab["000100000002"]["host"] == "203.0.113.232"
    assert by_fab["000100000002"]["deviceType"] == 8


def test_household_ids_include_semiprofessional_only_household(monkeypatch) -> None:
    _patch_browser(monkeypatch)
    monkeypatch.setitem(INFOS, WASHER, None)  # only the dishwasher answers

    groups = asyncio.run(mdns_household_ids(timeout=0, zeroconf=_FakeZeroconf()))

    assert groups == {GROUP}


def test_groups_by_hostname_include_semiprofessional(monkeypatch) -> None:
    _patch_browser(monkeypatch)

    hostnames = asyncio.run(_mdns_groups_by_hostname(timeout=0, zeroconf=_FakeZeroconf()))

    assert hostnames == {
        "miele-001d63fffe0a0a01.local": GROUP,
        "miele-001d63fffe0a0a02.local": GROUP,
    }


def test_push_listener_advertises_under_every_service_type() -> None:
    """A PG8133 browses only `_mielesemiprof._tcp` for peers, so the listener
    must be findable there as well as under `_mieleathome._tcp`."""
    from custom_components.miele_lan.push_listener import MielePushListener

    class _RecordingZeroconf:
        def __init__(self) -> None:
            self.registered: list = []
            self.unregistered: list = []

        async def async_register_service(self, info, allow_name_change=False):
            self.registered.append(info)

        async def async_unregister_service(self, info):
            self.unregistered.append(info)

    async def _noop(_event) -> None:
        return None

    zc = _RecordingZeroconf()
    listener = MielePushListener(
        our_fab="000999000001", group_id=GROUP, group_key="00" * 64,
        host_ip="203.0.113.226", port=18082, on_push=_noop, zeroconf=zc,
    )

    async def _run() -> None:
        await listener._advertise_mdns()
        await listener.stop()

    asyncio.run(_run())

    assert [i.type for i in zc.registered] == list(MIELE_SERVICE_TYPES)
    assert [i.name for i in zc.registered] == [
        f"HomeAssistant 000999000001.{st}" for st in MIELE_SERVICE_TYPES
    ]
    assert {i.server for i in zc.registered} == {listener.hostname}
    assert zc.unregistered == zc.registered
