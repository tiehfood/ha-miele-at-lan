"""Sleep-vs-unsupported handling for the DOP2 2/1585 (device context) poll.

Regression tests for the wake-and-retry path added with the dishwasher
consumable sensors: a sleeping XKM answers HTTP 500 (empty body) to
every DOP2 read, and latching *that* as "unsupported" would permanently
hide AutoDos/salt/rinse-aid entities on an appliance that happened to
be in deep standby at HA startup.

Coordinator is constructed standalone (no HA imports): a minimal stub
client reports scripted statuses, and the real
`_maybe_refresh_device_context` runs against it.
"""

import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import custom_components.miele_lan.coordinator as coordinator_mod  # noqa: E402
from custom_components.miele_lan.const import MieleAppliance  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "g7655_dop2_1585.hex"
LEAF_URL_BODY = (200, bytes.fromhex(FIXTURE.read_text().replace("\n", "")))


class _StubRaw:
    def __init__(self, statuses: list[int]) -> None:
        self._statuses = list(statuses)
        self.calls = 0

    async def _request_bytes(self, method: str, resource: str, **kwargs: Any):
        self.calls += 1
        status = self._statuses.pop(0)
        if status == 200:
            return LEAF_URL_BODY
        return (status, b"")


class _StubClient:
    def __init__(self, statuses: list[int]) -> None:
        self.raw = _StubRaw(statuses)
        self.wake = AsyncMock(return_value={})

    def set_statuses(self, statuses: list[int]) -> None:
        self.raw._statuses = list(statuses)
        self.raw.calls = 0


def _make_coordinator(statuses: list[int], device_type: MieleAppliance) -> Any:
    """Build a coordinator-shaped object without running DataUpdateCoordinator.__init__."""
    coord = coordinator_mod.MieleLanCoordinator.__new__(  # type: ignore[misc]
        coordinator_mod.MieleLanCoordinator
    )
    coord.client = _StubClient(statuses)  # type: ignore[assignment]
    coord.fab = "000106908768"
    # device_type is a read-only property derived from ident (self._data).
    coord._data = coordinator_mod.MieleLanData()
    coord._data.ident = {"device_type": int(device_type)}
    coord._device_context_unsupported = False
    coord._device_context_last_fetch = float("-inf")  # fresh runners: monotonic() starts ~0
    coord._device_context_miss_cycles = 0
    return coord


def _reset_throttle(coord: Any) -> None:
    coord._device_context_last_fetch = float("-inf")  # fresh runners: monotonic() starts ~0


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wake path sleeps ~3s against a live XKM; tests must not."""
    async def _instant(_delay: float) -> None:  # pragma: no cover - trivial
        return None

    monkeypatch.setattr(coordinator_mod.asyncio, "sleep", _instant)


async def _run(coord: Any) -> None:
    # 1585 was already fetched once at patch time... no: throttle only
    # applies to the timestamp, which _reset_throttle clears.
    t0 = time.monotonic()
    await coord._maybe_refresh_device_context()
    assert time.monotonic() - t0 < 10


def test_sleep_500_does_not_latch_unsupported() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500])
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False


def test_sleep_500_wakes_and_retries() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500, 200])
    asyncio.run(_run(coord))
    assert coord.client.raw.calls == 2
    # device context decoded from the retried read
    assert coord._data.device_context.get("salt_percent") == 77


def test_404_latches_unsupported() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([404])
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is True


def test_still_500_after_wake_stays_transient() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500, 500])
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False
    assert coord._device_context_miss_cycles == 1
    assert coord.client.raw.calls == 2


def test_miss_cycles_latch_after_three_without_200() -> None:
    """PR-review bound: 3 consecutive cycles ending without a 200 latch the leaf."""
    # Cycle 1 and 2: 500 -> wake -> still 500.
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500, 500])
    asyncio.run(_run(coord))
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is True


def test_miss_latch_clears_cached_device_context() -> None:
    """Latch must not leave frozen stale levels behind (review note)."""
    coord = _make_coordinator([200], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([200])
    asyncio.run(_run(coord))
    assert coord._data.device_context.get("salt_percent") == 77
    # Already 2 seeded misses; one more miss cycle latches + clears.
    coord._device_context_miss_cycles = 2
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is True
    assert coord._data.device_context == {}


def test_404_latch_clears_cached_device_context() -> None:
    coord = _make_coordinator([200], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([200])
    asyncio.run(_run(coord))
    assert coord._data.device_context
    coord.client.set_statuses([404])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is True
    assert coord._data.device_context == {}


def test_first_and_second_miss_never_latch() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500, 500])
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False


def test_200_resets_the_miss_counter() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.set_statuses([500, 500])
    asyncio.run(_run(coord))  # miss 1
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))  # miss 2
    assert coord._device_context_miss_cycles == 2
    # A 200 on one cycle clears the streak entirely…
    coord.client.set_statuses([200])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_miss_cycles == 0
    assert coord._device_context_unsupported is False
    # …so two further misses (not one) are needed to latch.
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is False
    coord.client.set_statuses([500, 500])
    _reset_throttle(coord)
    asyncio.run(_run(coord))
    assert coord._device_context_unsupported is True


def test_wake_failure_counts_as_a_miss() -> None:
    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.wake = AsyncMock(side_effect=RuntimeError("no route"))
    coord.client.set_statuses([500])
    asyncio.run(_run(coord))
    assert coord._device_context_miss_cycles == 1
    assert coord._device_context_unsupported is False


def test_fetch_failure_counts_as_a_miss() -> None:
    """A timeout on the first read is a cycle that ended without a 200."""

    class _BoomRaw(_StubRaw):
        async def _request_bytes(self, method: str, resource: str, **kwargs: Any):
            raise TimeoutError()

    coord = _make_coordinator([500], MieleAppliance.DISHWASHER)
    coord.client.raw = _BoomRaw([])  # type: ignore[assignment]
    coord.client.raw.calls = 0
    asyncio.run(_run(coord))
    assert coord._device_context_miss_cycles == 1
    assert coord._device_context_unsupported is False


def test_non_dw_family_never_polls() -> None:
    coord = _make_coordinator([500], MieleAppliance.OVEN)
    coord.client.set_statuses([500, 200])
    asyncio.run(_run(coord))
    assert coord.client.raw.calls == 0


def test_laundry_family_still_polls() -> None:
    """Gate extension must not regress the original TwinDos consumers."""
    coord = _make_coordinator([500], MieleAppliance.WASHING_MACHINE)
    coord.client.set_statuses([200])
    asyncio.run(_run(coord))
    assert coord.client.raw.calls == 1
    assert coord._data.device_context.get("dos_containers") is not None

@pytest.mark.parametrize(
    "device_type",
    [MieleAppliance.TUMBLE_DRYER, MieleAppliance.WASHING_MACHINE],
)
def test_sleeping_laundry_appliance_is_not_woken(device_type: MieleAppliance) -> None:
    """Waking a washer or dryer switches it on (#65); skip the cycle instead."""
    coord = _make_coordinator([500], device_type)
    coord.client.set_statuses([500])
    asyncio.run(_run(coord))
    coord.client.wake.assert_not_awaited()
    assert coord.client.raw.calls == 1
    assert coord._device_context_miss_cycles == 0
    assert coord._device_context_unsupported is False
