"""`MissedApplianceWatcher`: reload once an appliance setup missed is back.

Driven without Home Assistant: a fake `hass` records reload requests, a fake
mDNS browser replays announcements, and a fake probe stands in for the
signed `GET /Devices`.
"""

import asyncio
import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import recovery as recovery_mod  # noqa: E402
from custom_components.miele_lan.recovery import (  # noqa: E402
    DATA_RECOVERY_RELOADS,
    MAX_RELOAD_GAP,
    MIN_RELOAD_GAP,
    MissedApplianceWatcher,
    note_setup_result,
)

GROUP = "0123456789ABCDEF"
HA_FAB = "000999000001"
MISSING = "000100000002"
ENROLLED_IP = "203.0.113.116"
MISSING_IP = "203.0.113.232"
SEMIPROF = "_mielesemiprof._tcp.local."


class _FakeConfigEntries:
    def __init__(self) -> None:
        self.reloads: list[str] = []

    def async_schedule_reload(self, entry_id: str) -> None:
        self.reloads.append(entry_id)


class _FakeHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.config_entries = _FakeConfigEntries()


class _Info:
    def __init__(self, ip: str, group: str = GROUP) -> None:
        self.properties = {b"group": group.encode()}
        self._ip = ip

    def parsed_addresses(self) -> list[str]:
        return [self._ip]


class _FakeZeroconf:
    def __init__(self, infos: dict[str, _Info]) -> None:
        self.zeroconf = object()
        self._infos = infos

    async def async_get_service_info(self, service_type, name, timeout):
        return self._infos.get(name)


def _patch_browser(monkeypatch, names: list[str]) -> None:
    from zeroconf import ServiceStateChange

    class _Browser:
        def __init__(self, zc, service_types, handlers) -> None:
            for name in names:
                handlers[0](None, SEMIPROF, name, ServiceStateChange.Added)

        async def async_cancel(self) -> None:
            return None

    monkeypatch.setattr("zeroconf.asyncio.AsyncServiceBrowser", _Browser)


def _watcher(hass, *, probe=None, zeroconf=None, **kwargs) -> MissedApplianceWatcher:
    async def _no_probe(ip):
        raise AssertionError(f"unexpected probe of {ip}")

    return MissedApplianceWatcher(
        hass=hass,
        entry_id="entry1",
        missing_fabs={MISSING},
        enrolled_ips={ENROLLED_IP},
        group_id=GROUP,
        exclude_ha_fab=HA_FAB,
        probe=probe or _no_probe,
        zeroconf=zeroconf,
        create_task=lambda coro, name: asyncio.get_running_loop().create_task(coro),
        announce_debounce=0,
        **kwargs,
    )


def _run(coro_fn):
    async def _main():
        return await coro_fn()

    return asyncio.run(_main())


def test_push_from_missed_appliance_reloads_once() -> None:
    hass = _FakeHass()
    watcher = _watcher(hass)

    assert watcher.notify_push(MISSING) is True
    assert watcher.notify_push(MISSING) is True
    assert hass.config_entries.reloads == ["entry1"]


def test_push_from_unknown_fab_is_not_handled() -> None:
    hass = _FakeHass()
    watcher = _watcher(hass)

    assert watcher.notify_push("000000000042") is False
    assert hass.config_entries.reloads == []


def test_reloads_are_rate_limited_across_reloads() -> None:
    hass = _FakeHass()
    _watcher(hass).notify_push(MISSING)

    # The reload re-runs setup; the appliance is missed again and a fresh
    # watcher starts. It must not reload straight away.
    second = _watcher(hass)
    second.notify_push(MISSING)

    assert hass.config_entries.reloads == ["entry1"]
    assert hass.data[DATA_RECOVERY_RELOADS]["entry1"].pending == {MISSING}


def test_announcement_of_missed_appliance_reloads(monkeypatch) -> None:
    name = f"Miele PG8133.{SEMIPROF}"
    _patch_browser(monkeypatch, [name])
    probed: list[str] = []

    async def _probe(ip):
        probed.append(ip)
        return [MISSING]

    hass = _FakeHass()

    async def _main():
        watcher = _watcher(
            hass, probe=_probe, zeroconf=_FakeZeroconf({name: _Info(MISSING_IP)}),
        )
        await watcher.async_start()
        await asyncio.sleep(0.05)
        await watcher.async_stop()

    _run(_main)

    assert probed == [MISSING_IP]
    assert hass.config_entries.reloads == ["entry1"]


def test_announcements_that_cannot_be_a_missed_appliance_are_not_probed(monkeypatch) -> None:
    names = {
        "Miele WWV980._mieleathome._tcp.local.": _Info(ENROLLED_IP),
        f"Miele G7000.{SEMIPROF}": _Info("203.0.113.9", group="AAAAAAAAAAAAAAAA"),
        f"HomeAssistant {HA_FAB}.{SEMIPROF}": _Info("203.0.113.226"),
    }
    _patch_browser(monkeypatch, list(names))
    hass = _FakeHass()

    async def _main():
        watcher = _watcher(hass, zeroconf=_FakeZeroconf(names))
        await watcher.async_start()
        await asyncio.sleep(0.05)
        await watcher.async_stop()

    _run(_main)

    assert hass.config_entries.reloads == []


def test_periodic_check_retries_an_address_that_did_not_answer(monkeypatch) -> None:
    name = f"Miele PG8133.{SEMIPROF}"
    _patch_browser(monkeypatch, [name])
    answers = [[], [MISSING]]  # asleep on the announcement, awake later

    async def _probe(ip):
        return answers.pop(0) if answers else [MISSING]

    hass = _FakeHass()

    async def _main():
        watcher = _watcher(
            hass, probe=_probe, zeroconf=_FakeZeroconf({name: _Info(MISSING_IP)}),
            check_interval=0.05,
        )
        await watcher.async_start()
        await asyncio.sleep(0.02)
        assert hass.config_entries.reloads == []
        await asyncio.sleep(0.1)
        await watcher.async_stop()

    _run(_main)

    assert hass.config_entries.reloads == ["entry1"]


def test_nothing_missing_starts_nothing(monkeypatch) -> None:
    def _browser(*args, **kwargs):
        raise AssertionError("no browser expected")

    monkeypatch.setattr("zeroconf.asyncio.AsyncServiceBrowser", _browser)
    hass = _FakeHass()

    async def _main():
        watcher = MissedApplianceWatcher(
            hass=hass, entry_id="entry1", missing_fabs=set(), enrolled_ips=set(),
            group_id=GROUP, exclude_ha_fab=HA_FAB, probe=None,  # type: ignore[arg-type]
            zeroconf=_FakeZeroconf({}),
            create_task=lambda coro, name: asyncio.get_running_loop().create_task(coro),
        )
        await watcher.async_start()
        await watcher.async_stop()

    _run(_main)

    assert hass.config_entries.reloads == []


class _Clock:
    def __init__(self, monkeypatch) -> None:
        self.now = 1000.0
        monkeypatch.setattr(recovery_mod.time, "monotonic", lambda: self.now)


def _setup_then_push_until_reload(hass, clock, missing: set[str]) -> float:
    """One setup that missed `missing`, then a push from MISSING every
    minute until one schedules a reload. Returns the time of that reload."""
    note_setup_result(hass, "entry1", missing)
    watcher = _watcher(hass)
    before = len(hass.config_entries.reloads)
    for _ in range(24 * 60):
        watcher.notify_push(MISSING)
        if len(hass.config_entries.reloads) > before:
            return clock.now
        clock.now += 60
    raise AssertionError("no reload within a day")


def test_gap_grows_while_recovery_reloads_do_not_bring_the_appliance_back(
    monkeypatch,
) -> None:
    """Seen with a dryer that pushes but answers `GET /State` with 404 during
    every first refresh: each push used to reload the entry every 5 minutes,
    taking every appliance unavailable each time."""
    clock = _Clock(monkeypatch)
    hass = _FakeHass()

    reload_times = [_setup_then_push_until_reload(hass, clock, {MISSING}) for _ in range(9)]

    gaps = [b - a for a, b in itertools.pairwise(reload_times)]
    assert gaps == [
        2 * MIN_RELOAD_GAP, 4 * MIN_RELOAD_GAP, 8 * MIN_RELOAD_GAP,
        16 * MIN_RELOAD_GAP, 32 * MIN_RELOAD_GAP, 64 * MIN_RELOAD_GAP,
        MAX_RELOAD_GAP, MAX_RELOAD_GAP,
    ]


def test_gap_grows_once_per_recovery_reload_not_per_setup(monkeypatch) -> None:
    _Clock(monkeypatch)
    hass = _FakeHass()
    _watcher(hass).notify_push(MISSING)

    note_setup_result(hass, "entry1", {MISSING})  # the recovery reload's setup
    note_setup_result(hass, "entry1", {MISSING})  # a manual reload after it

    assert hass.data[DATA_RECOVERY_RELOADS]["entry1"].gap == 2 * MIN_RELOAD_GAP


def test_gap_starts_over_once_the_appliance_is_back(monkeypatch) -> None:
    clock = _Clock(monkeypatch)
    hass = _FakeHass()
    _setup_then_push_until_reload(hass, clock, {MISSING})
    _setup_then_push_until_reload(hass, clock, {MISSING})
    assert hass.data[DATA_RECOVERY_RELOADS]["entry1"].gap == 2 * MIN_RELOAD_GAP

    # This reload brought it back; another appliance is still missing.
    note_setup_result(hass, "entry1", {"000100000003"})
    assert hass.data[DATA_RECOVERY_RELOADS]["entry1"].gap == MIN_RELOAD_GAP

    # A setup with nothing missing forgets the history altogether.
    note_setup_result(hass, "entry1", set())
    assert "entry1" not in hass.data[DATA_RECOVERY_RELOADS]

