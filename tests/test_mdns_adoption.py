"""Tests for adopting mDNS-confirmed appliances the cloud device list omits.

`_merge_mdns_adoptions` lives in `__init__.py`; `enroll_all`'s missing-fab
warning lives in `enrollment.py`. Both are pure/async-stubbable without
touching HA's config-entry machinery.
"""

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import _merge_mdns_adoptions  # noqa: E402
from custom_components.miele_lan import enrollment as enrollment_mod  # noqa: E402
from custom_components.miele_lan.enrollment import (  # noqa: E402
    enroll_all,
    mdns_discover_household,
)
from custom_components.miele_lan.push_listener import DEFAULT_DEVICE_TYPE  # noqa: E402

FAB_LISTED = "000000000001"
FAB_LISTED_LEGACY_KEY = "000000000002"
FAB_UNLISTED = "000000000003"


def _mdns_hit(fab: str, device_type: int = 0) -> dict:
    return {"fabNr": fab, "deviceType": device_type, "deviceName": "",
            "host": "203.0.113.1", "hostname": "Miele-example.local."}


# ------------------------------------------------------- _merge_mdns_adoptions
def test_adopts_only_the_fab_missing_from_a_partial_cloud_list() -> None:
    devices = [{"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""}]
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_UNLISTED)]

    merged, adopted = _merge_mdns_adoptions(devices, mdns_results)

    assert [d["fabNr"] for d in adopted] == [FAB_UNLISTED]
    assert {d.get("fabNr") or d.get("fab") for d in merged} == {
        FAB_LISTED, FAB_UNLISTED,
    }
    assert len(merged) == 2


def test_recognises_fab_under_either_fabnr_or_fab_key_and_does_not_duplicate() -> None:
    devices = [
        {"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""},
        {"fab": FAB_LISTED_LEGACY_KEY},
    ]
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_LISTED_LEGACY_KEY)]

    merged, adopted = _merge_mdns_adoptions(devices, mdns_results)

    assert adopted == []
    assert len(merged) == 2


def test_empty_cloud_list_still_adopts_everything_mdns_found() -> None:
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_UNLISTED)]

    merged, adopted = _merge_mdns_adoptions([], mdns_results)

    assert {d["fabNr"] for d in adopted} == {FAB_LISTED, FAB_UNLISTED}
    assert merged == adopted


def test_mdns_finding_nothing_changes_nothing() -> None:
    devices = [{"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""}]

    merged, adopted = _merge_mdns_adoptions(devices, [])

    assert adopted == []
    assert merged == devices


# --------------------------------------------------------------- enroll_all
class _NeverCalledResolver:
    zeroconf = None

    async def resolve(self, fab: str) -> str | None:
        raise AssertionError(f"resolve() should not be called for {fab!r}")


def test_enroll_all_warns_and_skips_worklist_entry_with_no_fab_key(
    caplog,
) -> None:
    devices = [{"deviceType": 1, "deviceName": "no fab here"}]

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(enroll_all(
            devices,
            group_id_hex="00" * 16,
            group_key_hex="00" * 64,
            ha_fab="000000000000",
            ha_hostname="ha.local.",
            ha_port=12345,
            resolver=_NeverCalledResolver(),
        ))

    assert result.enrolled == []
    assert result.failed == []
    assert any(
        record.levelno == logging.WARNING and "no fab number" in record.getMessage()
        for record in caplog.records
    )


# ------------------------------------------------------ mdns_discover_household
GROUP = "AA" * 16
HA_FAB = "000000000099"
NORMAL_IP = "203.0.113.10"
NORMAL_FAB = "000000000010"


class _FakeServiceInfo:
    def __init__(self, *, group: str, ip: str, devicetype: int | None, server: str) -> None:
        props: dict[bytes, bytes] = {b"group": group.encode()}
        if devicetype is not None:
            props[b"devicetype"] = str(devicetype).encode()
        self.properties = props
        self.server = server
        self._ip = ip

    def parsed_addresses(self) -> list[str]:
        return [self._ip]


class _FakeAsyncZeroconf:
    def __init__(self, infos: dict[str, _FakeServiceInfo]) -> None:
        self._infos = infos
        self.zeroconf = object()

    async def async_get_service_info(self, service_type, name, timeout):
        return self._infos.get(name)

    async def async_close(self) -> None:
        return None


def _fake_browser_factory(names: list[str]):
    from zeroconf import ServiceStateChange

    class _FakeAsyncServiceBrowser:
        def __init__(self, zc, service_types, handlers) -> None:
            handler = handlers[0]
            for name in names:
                handler(None, service_types[0], name, ServiceStateChange.Added)

        async def async_cancel(self) -> None:
            return None

    return _FakeAsyncServiceBrowser


def _discover(monkeypatch, infos: dict[str, _FakeServiceInfo], fabs_by_ip: dict[str, list[str]],
              **kwargs) -> tuple[list[dict], list[str]]:
    """Run the real `mdns_discover_household` against faked mDNS hits.

    Only the two network edges (the mDNS browse and the signed GET /Devices
    probe) are stubbed; the group/exclude filtering under test is the
    production code path.
    """
    monkeypatch.setattr(
        "zeroconf.asyncio.AsyncServiceBrowser", _fake_browser_factory(list(infos.keys()))
    )
    probed_ips: list[str] = []

    async def _fake_signed_get_fabs(*, ip, group_id_hex, group_key_hex):
        probed_ips.append(ip)
        return fabs_by_ip.get(ip, [])

    monkeypatch.setattr(enrollment_mod, "_signed_get_fabs", _fake_signed_get_fabs)

    results = asyncio.run(mdns_discover_household(
        GROUP, "00" * 64, timeout=0, zeroconf=_FakeAsyncZeroconf(infos), **kwargs
    ))
    return results, probed_ips


def test_ignores_hit_advertising_our_own_ha_fab(monkeypatch) -> None:
    name = f"HomeAssistant {HA_FAB}._mieleathome._tcp.local."
    infos = {
        name: _FakeServiceInfo(
            group=GROUP, ip="203.0.113.11", devicetype=0, server="Miele-ha.local.",
        ),
    }

    results, probed = _discover(monkeypatch, infos, {}, exclude_ha_fab=HA_FAB)

    assert results == []
    assert probed == []


def test_ignores_hit_advertising_default_device_type_even_with_unknown_fab(
    monkeypatch,
) -> None:
    name = "Miele Oven._mieleathome._tcp.local."
    ip = "203.0.113.12"
    infos = {
        name: _FakeServiceInfo(
            group=GROUP, ip=ip, devicetype=DEFAULT_DEVICE_TYPE, server="Miele-other-ha.local.",
        ),
    }

    results, probed = _discover(monkeypatch, infos, {})

    assert results == []
    assert probed == []


def test_ignores_hit_from_our_own_lan_ip(monkeypatch) -> None:
    our_ip = "203.0.113.13"
    name = "Miele Oven._mieleathome._tcp.local."
    infos = {
        name: _FakeServiceInfo(
            group=GROUP, ip=our_ip, devicetype=0, server="Miele-oven.local.",
        ),
    }

    results, probed = _discover(monkeypatch, infos, {}, exclude_ip=our_ip)

    assert results == []
    assert probed == []


def test_excluded_ip_hit_logs_debug_when_auto_detected(monkeypatch, caplog) -> None:
    our_ip = "203.0.113.17"
    name = "Miele Oven._mieleathome._tcp.local."
    infos = {
        name: _FakeServiceInfo(
            group=GROUP, ip=our_ip, devicetype=0, server="Miele-oven.local.",
        ),
    }

    with caplog.at_level(logging.DEBUG):
        _discover(monkeypatch, infos, {}, exclude_ip=our_ip)

    assert not any(
        record.levelno == logging.WARNING and our_ip in record.getMessage()
        for record in caplog.records
    )
    assert any(
        record.levelno == logging.DEBUG and our_ip in record.getMessage()
        for record in caplog.records
    )


def test_excluded_ip_hit_logs_warning_when_configured(monkeypatch, caplog) -> None:
    """A typo'd appliance IP in the advertise_address option must not vanish
    behind a DEBUG line that claims it was "our own IP" (issue behind
    dd19438) — it needs a WARNING naming the option so the user can fix it."""
    configured_ip = "203.0.113.18"
    name = "Miele Oven._mieleathome._tcp.local."
    infos = {
        name: _FakeServiceInfo(
            group=GROUP, ip=configured_ip, devicetype=0, server="Miele-oven.local.",
        ),
    }

    with caplog.at_level(logging.DEBUG):
        _discover(
            monkeypatch, infos, {},
            exclude_ip=configured_ip, exclude_ip_is_configured=True,
        )

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and configured_ip in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "advertise_address" in warnings[0].getMessage()


def test_normal_appliance_hit_still_returned_with_all_exclusions_active(
    monkeypatch,
) -> None:
    our_ip = "203.0.113.14"
    normal_name = "Miele Oven._mieleathome._tcp.local."
    ha_fab_name = f"HomeAssistant {HA_FAB}._mieleathome._tcp.local."
    default_type_name = "Miele Fridge._mieleathome._tcp.local."
    infos = {
        normal_name: _FakeServiceInfo(
            group=GROUP, ip=NORMAL_IP, devicetype=1, server="Miele-oven.local.",
        ),
        ha_fab_name: _FakeServiceInfo(
            group=GROUP, ip="203.0.113.15", devicetype=0, server="Miele-ha.local.",
        ),
        default_type_name: _FakeServiceInfo(
            group=GROUP, ip="203.0.113.16", devicetype=DEFAULT_DEVICE_TYPE,
            server="Miele-other-ha.local.",
        ),
        "self-ip._mieleathome._tcp.local.": _FakeServiceInfo(
            group=GROUP, ip=our_ip, devicetype=0, server="Miele-self-ip.local.",
        ),
    }
    fabs_by_ip = {NORMAL_IP: [NORMAL_FAB]}

    results, probed = _discover(
        monkeypatch, infos, fabs_by_ip, exclude_ha_fab=HA_FAB, exclude_ip=our_ip,
    )

    assert probed == [NORMAL_IP]
    assert results == [{
        "fabNr": NORMAL_FAB,
        "deviceType": 1,
        "deviceName": "",
        "host": NORMAL_IP,
        "hostname": "Miele-oven.local",
    }]
