"""Setup must not wait for appliances one after another.

An appliance in standby can take the full signed-request timeout to answer.
Enrolling (and later cleaning up) appliances sequentially made setup as slow
as the sum of those waits; these tests pin the concurrent behaviour.
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import enrollment as enrollment_mod  # noqa: E402
from custom_components.miele_lan.enrollment import (  # noqa: E402
    DeviceIpResolver,
    EnrolledDevice,
    FailedDevice,
    enroll_all,
)

SLOW = 0.3


def _run_enroll_all(monkeypatch, devices, static):
    async def _slow_enroll_device(*, fab, host_ip, **_kwargs):
        await asyncio.sleep(SLOW)
        return EnrolledDevice(
            fab=fab, host_ip=host_ip, route=fab, supervision_ok=False,
            subscriptions_ok=["/State/"], enrolled_at=0.0,
        )

    async def _no_refine(failed, **_kwargs):
        return failed

    monkeypatch.setattr(enrollment_mod, "enroll_device", _slow_enroll_device)
    monkeypatch.setattr(enrollment_mod, "_refine_failure_reasons", _no_refine)

    start = time.monotonic()
    result = asyncio.run(enroll_all(
        devices,
        group_id_hex="00" * 8,
        group_key_hex="00" * 64,
        ha_fab="000999000001",
        ha_hostname="ha.local.",
        ha_port=18082,
        resolver=DeviceIpResolver(static=static),
        session=None,  # type: ignore[arg-type]
    ))
    return result, time.monotonic() - start


def test_enroll_all_waits_for_the_slowest_appliance_not_the_sum(monkeypatch) -> None:
    devices = [{"fabNr": f"00000000000{i}"} for i in range(1, 5)]
    static = {d["fabNr"]: f"203.0.113.{i}" for i, d in enumerate(devices, start=1)}

    result, elapsed = _run_enroll_all(monkeypatch, devices, static)

    assert [e.fab for e in result.enrolled] == [d["fabNr"] for d in devices]
    assert elapsed < SLOW * 2, f"took {elapsed:.2f}s for 4 appliances"


def test_enroll_all_keeps_unresolvable_appliances_as_failures(monkeypatch) -> None:
    devices = [{"fabNr": "000000000001"}, {"fabNr": "000000000002"}]

    result, _ = _run_enroll_all(monkeypatch, devices, {"000000000001": "203.0.113.1"})

    assert [e.fab for e in result.enrolled] == ["000000000001"]
    assert [f.fab for f in result.failed] == ["000000000002"]
    assert isinstance(result.failed[0], FailedDevice)


def test_resolver_answers_unknown_fab_without_an_mdns_browse() -> None:
    """A fab number does not encode the appliance's MAC-based hostname, so a
    per-fab mDNS lookup cannot succeed; it used to cost a 4 s browse anyway."""
    resolver = DeviceIpResolver(static={"000000000001": "203.0.113.1"})

    async def _resolve():
        start = time.monotonic()
        known = await resolver.resolve("000000000001")
        unknown = await resolver.resolve("000000000002")
        return known, unknown, time.monotonic() - start

    known, unknown, elapsed = asyncio.run(_resolve())

    assert known == "203.0.113.1"
    assert unknown is None
    assert elapsed < 0.1


class _FakeRaw:
    def __init__(self, listing: bytes | None, live: dict[int, str]) -> None:
        self._listing = listing
        self._live = live
        self.requests: list[tuple[str, str]] = []

    async def _request_bytes(self, method, resource, allowed_status=(200,), body=None):
        self.requests.append((method, resource))
        if resource == "/Subscriptions/":
            if self._listing is None:
                raise RuntimeError("no listing on this firmware")
            return 200, self._listing
        n = int(resource.strip("/").split("/")[-1])
        if method == "GET":
            return (200, self._live[n].encode()) if n in self._live else (404, b"")
        self._live.pop(n, None)
        return 204, b""


class _FakeClient:
    def __init__(self, raw: _FakeRaw) -> None:
        self.raw = raw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _cleanup(monkeypatch, listing, live):
    raw = _FakeRaw(listing, live)
    monkeypatch.setattr(
        enrollment_mod.MieleLanClient, "from_hex",
        classmethod(lambda cls, *a, **k: _FakeClient(raw)),
    )
    deleted = asyncio.run(enrollment_mod.cleanup_stale_subscriptions(
        host_ip="203.0.113.1", fab="000000000001",
        group_id_hex="00" * 8, group_key_hex="00" * 64, our_ha_fab="000999000001",
    ))
    return deleted, raw


OURS = '{"Callback":"http://Miele-x.local.:18082/Devices/000999000001/SuperVision/1/State/"}'
STALE = '{"Callback":"http://Miele-y.local.:18082/Devices/000999000002/SuperVision/1/State/"}'


def test_cleanup_reads_only_the_listed_slots(monkeypatch) -> None:
    listing = b'{\n\t"1":{"href":"1/"},\n\t"7":{"href":"7/"}\n}  '
    deleted, raw = _cleanup(monkeypatch, listing, {1: OURS, 7: STALE})

    assert deleted == 1
    assert raw.requests == [
        ("GET", "/Subscriptions/"),
        ("GET", "/Subscriptions/1/"),
        ("GET", "/Subscriptions/7/"),
        ("DELETE", "/Subscriptions/7/"),
    ]


def test_cleanup_scans_slot_numbers_when_the_listing_fails(monkeypatch) -> None:
    deleted, raw = _cleanup(monkeypatch, None, {1: OURS, 7: STALE})

    assert deleted == 1
    gets = [r for m, r in raw.requests if m == "GET" and r != "/Subscriptions/"]
    assert len(gets) == 7 + 16  # up to the last live slot, then 16 misses


def test_cleanup_does_not_scan_an_appliance_that_does_not_answer(monkeypatch) -> None:
    from asyncmiele.exceptions.network import NetworkTimeoutError

    class _SilentRaw(_FakeRaw):
        async def _request_bytes(self, method, resource, allowed_status=(200,), body=None):
            self.requests.append((method, resource))
            raise NetworkTimeoutError("")

    raw = _SilentRaw(None, {})
    monkeypatch.setattr(
        enrollment_mod.MieleLanClient, "from_hex",
        classmethod(lambda cls, *a, **k: _FakeClient(raw)),
    )
    deleted = asyncio.run(enrollment_mod.cleanup_stale_subscriptions(
        host_ip="203.0.113.1", fab="000000000001",
        group_id_hex="00" * 8, group_key_hex="00" * 64, our_ha_fab="000999000001",
    ))

    assert deleted == 0
    assert raw.requests == [("GET", "/Subscriptions/")]


def test_push_listener_registers_its_announcements_together() -> None:
    """Each mDNS registration probes the network for about a second, so the
    announcements under the two service types must not wait for each other."""
    from custom_components.miele_lan.const import MIELE_SERVICE_TYPES
    from custom_components.miele_lan.push_listener import MielePushListener

    class _SlowZeroconf:
        def __init__(self) -> None:
            self.registered: list = []

        async def async_register_service(self, info, allow_name_change=False):
            await asyncio.sleep(SLOW)
            self.registered.append(info)

        async def async_unregister_service(self, info):
            return None

    async def _noop(_event) -> None:
        return None

    zc = _SlowZeroconf()
    listener = MielePushListener(
        our_fab="000999000001", group_id="0123456789ABCDEF", group_key="00" * 64,
        host_ip="203.0.113.226", port=18082, on_push=_noop, zeroconf=zc,
    )

    start = time.monotonic()
    asyncio.run(listener._advertise_mdns())
    elapsed = time.monotonic() - start

    assert len(MIELE_SERVICE_TYPES) > 1
    assert elapsed < SLOW * 1.5, f"took {elapsed:.2f}s, registrations ran one after another"
    assert sorted(i.type for i in zc.registered) == sorted(MIELE_SERVICE_TYPES)
