"""Tests for the user-configurable mDNS advertise address (issue #45).

`resolve_advertise_ip` lives in `push_listener.py`; the `_setup_cloud` test
below drives the real production wiring in `__init__.py` far enough to
observe what address gets handed to both the push listener (`host_ip=`) and
the mDNS discovery browse (`exclude_ip=`), stubbing only the network edges
(mDNS, HTTP enrollment) the same way test_mdns_adoption.py does.
"""

import asyncio
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import custom_components.miele_lan as miele_lan  # noqa: E402
from custom_components.miele_lan.enrollment import EnrollmentResult  # noqa: E402
from custom_components.miele_lan.push_listener import resolve_advertise_ip  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402

DETECTED_IP = "203.0.113.5"
CONFIGURED_IP = "192.0.2.50"
GROUP_ID = "AA" * 8
GROUP_KEY = "BB" * 64
HA_FAB = "000000000099"
DEVICE_FAB = "000000000010"


# ------------------------------------------------------------ resolve_advertise_ip
def test_resolve_advertise_ip_unset_uses_detected() -> None:
    ip, is_configured = resolve_advertise_ip(configured=None, detected=DETECTED_IP)
    assert ip == DETECTED_IP
    assert is_configured is False


def test_resolve_advertise_ip_empty_string_uses_detected() -> None:
    ip, is_configured = resolve_advertise_ip(configured="", detected=DETECTED_IP)
    assert ip == DETECTED_IP
    assert is_configured is False


def test_resolve_advertise_ip_configured_overrides_detected() -> None:
    ip, is_configured = resolve_advertise_ip(configured=CONFIGURED_IP, detected=DETECTED_IP)
    assert ip == CONFIGURED_IP
    assert is_configured is True


# -------------------------------------------------------------------- _setup_cloud
class _FakeListener:
    """Records constructor kwargs; never actually binds a port or touches mDNS."""

    instances: list["_FakeListener"] = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.hostname = "Miele-fakehost.local."
        _FakeListener.instances.append(self)

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _FakeEntry:
    def __init__(self, data: dict, options: dict) -> None:
        self.data = data
        self.options = MappingProxyType(options)
        self.entry_id = "test_entry_id"

    def add_update_listener(self, cb):
        return lambda: None


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}


def _entry(*, advertise_address: str | None) -> _FakeEntry:
    options = {"advertise_address": advertise_address} if advertise_address else {}
    return _FakeEntry(
        data={
            "group_id": GROUP_ID,
            "group_key": GROUP_KEY,
            "devices": [{"fabNr": DEVICE_FAB, "deviceType": 1, "deviceName": ""}],
            "ha_fab": HA_FAB,
        },
        options=options,
    )


def _run_setup_cloud(monkeypatch, entry: _FakeEntry) -> dict:
    """Drive real `_setup_cloud` up to the point it observes the effective
    advertise IP, then short-circuit via the (real) "no devices reachable"
    path so we never touch actual sockets or HTTP."""
    _FakeListener.instances.clear()
    monkeypatch.setattr(miele_lan, "detect_lan_ip", lambda target: DETECTED_IP)
    monkeypatch.setattr(miele_lan, "MielePushListener", _FakeListener)

    class _FakeSharedZc:
        pass

    async def _fake_get_async_instance(hass):
        return _FakeSharedZc()

    monkeypatch.setattr(
        miele_lan.ha_zeroconf, "async_get_async_instance", _fake_get_async_instance
    )

    captured_mdns_kwargs: dict = {}

    async def _fake_mdns_discover_household(*, group_id_hex, group_key_hex, timeout,
                                              zeroconf, exclude_ha_fab, exclude_ip):
        captured_mdns_kwargs["exclude_ip"] = exclude_ip
        return []

    monkeypatch.setattr(miele_lan, "mdns_discover_household", _fake_mdns_discover_household)

    async def _fake_enroll_all(*args, **kwargs):
        return EnrollmentResult(enrolled=[], failed=[])

    monkeypatch.setattr(miele_lan, "enroll_all", _fake_enroll_all)

    hass = _FakeHass()
    with pytest.raises(ConfigEntryNotReady):
        asyncio.run(miele_lan._setup_cloud(hass, entry))

    assert len(_FakeListener.instances) == 1
    return {
        "listener_host_ip": _FakeListener.instances[0].kwargs["host_ip"],
        "mdns_exclude_ip": captured_mdns_kwargs["exclude_ip"],
    }


def test_unset_option_advertises_detected_ip(monkeypatch) -> None:
    result = _run_setup_cloud(monkeypatch, _entry(advertise_address=None))
    assert result["listener_host_ip"] == DETECTED_IP
    assert result["mdns_exclude_ip"] == DETECTED_IP


def test_configured_option_advertises_that_address(monkeypatch) -> None:
    result = _run_setup_cloud(monkeypatch, _entry(advertise_address=CONFIGURED_IP))
    assert result["listener_host_ip"] == CONFIGURED_IP


def test_configured_option_is_also_excluded_from_mdns_discovery(monkeypatch) -> None:
    """The configured address must be excluded too, not just the detected LAN
    IP — otherwise our own advertisement (re-broadcast at the configured
    address by a repeater) could be picked back up as a phantom appliance,
    reintroducing the bug fixed in v1.13.2."""
    result = _run_setup_cloud(monkeypatch, _entry(advertise_address=CONFIGURED_IP))
    assert result["mdns_exclude_ip"] == CONFIGURED_IP
    assert result["mdns_exclude_ip"] != DETECTED_IP
