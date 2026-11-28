"""Removing the integration cleans up on the appliances it was enrolled on.

A household found over mDNS has no stored address, so setup remembers where
each appliance was enrolled and `async_remove_entry` uses that.
"""
import asyncio
from types import MappingProxyType, SimpleNamespace

import pytest

import custom_components.miele_lan as miele_lan
from custom_components.miele_lan.const import DATA_ENROLLED_HOSTS

HA_FAB = "000999123456"
FAB = "000000000001"
OTHER_HA_FAB = "000999654321"


class _Raw:
    def __init__(self, slots: dict[int, str]) -> None:
        self.slots = slots
        self.calls: list[tuple[str, str]] = []

    async def _request_bytes(self, method, resource, **kwargs):
        self.calls.append((method, resource))
        if method == "GET":
            n = int(resource.strip("/").split("/")[-1])
            if n in self.slots:
                return 200, self.slots[n].encode()
            return 404, b""
        return 204, b""


@pytest.fixture
def clients(monkeypatch):
    created: list[tuple[str, str, _Raw]] = []
    slots = {
        1: '{"Callback":"Cloud"}',
        2: f'{{"Callback":"http://ha.local.:18082/Devices/{HA_FAB}/SuperVision/{FAB}/State/"}}',
        3: f'{{"Callback":"http://x.local.:18082/Devices/{OTHER_HA_FAB}/SuperVision/{FAB}/State/"}}',
    }

    class _Client:
        def __init__(self, raw: _Raw) -> None:
            self.raw = raw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

    def _from_hex(ip, group_id, group_key, fab, timeout):
        raw = _Raw(slots)
        created.append((ip, fab, raw))
        return _Client(raw)

    monkeypatch.setattr(miele_lan.MieleLanClient, "from_hex", staticmethod(_from_hex))
    monkeypatch.setattr(miele_lan.ir, "async_delete_issue", lambda *args: None)
    return created


def _entry(static_ips: dict[str, str] | None = None, flow_kind: str = "cloud"):
    return SimpleNamespace(
        entry_id="entry",
        data={
            "flow_kind": flow_kind,
            "group_id": "AA" * 8,
            "group_key": "BB" * 64,
            "ha_fab": HA_FAB,
        },
        options=MappingProxyType({"static_ips": static_ips} if static_ips else {}),
    )


def _hass(enrolled: dict[str, str] | None = None):
    data = {DATA_ENROLLED_HOSTS: {"entry": enrolled}} if enrolled is not None else {}
    return SimpleNamespace(data=data)


def test_mdns_household_is_cleaned_up_at_the_enrolled_address(clients) -> None:
    hass = _hass({FAB: "192.0.2.10"})
    asyncio.run(miele_lan.async_remove_entry(hass, _entry()))

    assert [(ip, fab) for ip, fab, _ in clients] == [("192.0.2.10", FAB)]
    calls = clients[0][2].calls
    assert ("PUT", f"/Devices/{FAB}/SuperVision/{HA_FAB}") in calls
    assert [r for m, r in calls if m == "DELETE"] == ["/Subscriptions/2/"]
    assert hass.data[DATA_ENROLLED_HOSTS] == {}


def test_enrolled_address_wins_over_a_configured_one(clients) -> None:
    hass = _hass({FAB: "192.0.2.10"})
    asyncio.run(miele_lan.async_remove_entry(hass, _entry({FAB: "192.0.2.99"})))
    assert [(ip, fab) for ip, fab, _ in clients] == [("192.0.2.10", FAB)]


def test_entry_that_never_loaded_falls_back_to_configured_addresses(clients) -> None:
    asyncio.run(miele_lan.async_remove_entry(_hass(), _entry({FAB: "192.0.2.99"})))
    assert [(ip, fab) for ip, fab, _ in clients] == [("192.0.2.99", FAB)]


def test_nothing_is_contacted_without_any_known_address(clients) -> None:
    asyncio.run(miele_lan.async_remove_entry(_hass(), _entry()))
    assert clients == []


def test_manual_entry_contacts_nothing(clients) -> None:
    hass = _hass({FAB: "192.0.2.10"})
    asyncio.run(miele_lan.async_remove_entry(hass, _entry(flow_kind="manual")))
    assert clients == []


def test_removing_the_entry_deletes_every_appliance_issue(clients, monkeypatch) -> None:
    deleted: list[str] = []
    monkeypatch.setattr(
        miele_lan.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id),
    )
    entry = _entry(static_ips={"000000000003": "192.0.2.30"})
    entry.data["devices"] = [{"fabNr": "000000000002"}]

    asyncio.run(miele_lan.async_remove_entry(_hass({FAB: "192.0.2.10"}), entry))

    assert sorted(deleted) == [
        "unenrolled_devices_entry",
        f"unenrolled_entry_{FAB}",
        "unenrolled_entry_000000000002",
        "unenrolled_entry_000000000003",
    ]
