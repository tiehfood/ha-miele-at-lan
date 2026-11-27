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
