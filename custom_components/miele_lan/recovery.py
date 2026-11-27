"""Bring back appliances that setup could not enrol, without a manual reload.

Setup enrols what answers while it runs. An appliance that is switched off,
in deep standby, or on flaky Wi-Fi misses that window, and before this module
it stayed unavailable until the user reloaded the integration by hand.

After setup, `MissedApplianceWatcher` watches for any sign that a missed
appliance is reachable, and then reloads the config entry, which re-runs the
normal setup path:

  * a push from the appliance (it still holds our `/Subscriptions` slots from
    an earlier run, so it pushes as soon as it is back);
  * an mDNS announcement of a household appliance at an address that is not
    enrolled, confirmed with one signed `GET /Devices` naming a missed fab;
  * a periodic re-check of those unenrolled addresses, for an appliance that
    announced itself but did not answer at that moment.

Reloads are rate-limited per config entry (`MIN_RELOAD_GAP`, kept across
reloads), so an appliance that keeps dropping off cannot make the
integration reload in a loop.

A reload briefly makes every appliance's entities unavailable. Adding the
missed appliance's coordinator and entities in place would avoid that, but
needs every platform to accept appliances after setup; this keeps the change
to one place.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any

from .const import DOMAIN, MIELE_SERVICE_TYPES

_LOGGER = logging.getLogger(__name__)

CHECK_INTERVAL = 600.0  # seconds between re-checks of unenrolled addresses
MIN_RELOAD_GAP = 300.0  # seconds between two recovery reloads of one entry
ANNOUNCE_DEBOUNCE = 5.0  # seconds to collect announcements before probing

DATA_LAST_RECOVERY_RELOAD = f"{DOMAIN}_last_recovery_reload"

Probe = Callable[[str], Awaitable[list[str]]]
"""Signed `GET /Devices` against an IP; returns the fab numbers it lists."""


class MissedApplianceWatcher:
    """Reload the entry once an appliance that setup missed is reachable."""

    def __init__(
        self,
        *,
        hass: Any,
        entry_id: str,
        missing_fabs: set[str],
        enrolled_ips: set[str],
        group_id: str,
        exclude_ha_fab: str,
        probe: Probe,
        zeroconf: Any | None,
        create_task: Callable[[Coroutine[Any, Any, Any], str], asyncio.Task],
        check_interval: float = CHECK_INTERVAL,
        min_reload_gap: float = MIN_RELOAD_GAP,
        announce_debounce: float = ANNOUNCE_DEBOUNCE,
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._missing = set(missing_fabs)
        self._enrolled_ips = set(enrolled_ips)
        self._group_id = group_id.upper()
        self._exclude_ha_fab = exclude_ha_fab
        self._probe = probe
        self._zc = zeroconf
        self._create_task = create_task
        self._check_interval = check_interval
        self._min_reload_gap = min_reload_gap
        self._announce_debounce = announce_debounce
        self._candidates: set[str] = set()  # unenrolled household IPs seen on mDNS
        self._browser: Any | None = None
        self._tasks: set[asyncio.Task] = set()
        self._probe_scheduled = False
        self._reload_requested = False

    @property
    def missing_fabs(self) -> set[str]:
        return set(self._missing)

    async def async_start(self) -> None:
        if not self._missing:
            return
        _LOGGER.info(
            "watching for missed appliance(s) %s — the integration reloads "
            "once one is reachable", ", ".join(sorted(self._missing)),
        )
        self._spawn(self._periodic_checks(), "periodic re-check")
        if self._zc is None:
            return
        try:
            from zeroconf import ServiceStateChange
            from zeroconf.asyncio import AsyncServiceBrowser
        except ImportError:
            return

        def _on_state(zeroconf, service_type, name, state_change):  # type: ignore[no-untyped-def]
            if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
                self._spawn(self._on_announcement(service_type, name), "mDNS announcement")

        self._browser = AsyncServiceBrowser(
            self._zc.zeroconf, list(MIELE_SERVICE_TYPES), handlers=[_on_state]
        )

    async def async_stop(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.async_cancel()
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("recovery browser cancel failed: %s", err)
            self._browser = None
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def notify_push(self, fab: str) -> bool:
        """A push arrived for a fab with no coordinator. True if it was a
        missed appliance (and a reload was requested)."""
        if fab not in self._missing:
            return False
        self._request_reload(f"appliance {fab} pushed an update")
        return True

    # ------------------------------------------------------------ internals

    def _spawn(self, coro: Coroutine[Any, Any, Any], what: str) -> None:
        task = self._create_task(coro, f"{DOMAIN} recovery: {what}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _on_announcement(self, service_type: str, name: str) -> None:
        if self._exclude_ha_fab and self._exclude_ha_fab in name:
            return  # our own push listener
        info = await self._zc.async_get_service_info(service_type, name, timeout=2000)
        if info is None:
            return
        props = {
            (k.decode("ascii", errors="ignore") if isinstance(k, bytes) else k):
            (v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        if (props.get("group") or "").upper() != self._group_id:
            return
        ips = [ip for ip in info.parsed_addresses() if ip not in self._enrolled_ips]
        if not ips:
            return
        self._candidates.update(ips)
        if self._probe_scheduled:
            return
        self._probe_scheduled = True
        # Appliances announce in bursts; probe once per burst.
        await asyncio.sleep(self._announce_debounce)
        self._probe_scheduled = False
        await self._check_candidates()

    async def _periodic_checks(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval)
            await self._check_candidates()

    async def _check_candidates(self) -> None:
        if self._reload_requested:
            return
        for ip in sorted(self._candidates):
            try:
                fabs = await self._probe(ip)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("recovery probe of %s failed: %s", ip, err)
                continue
            found = self._missing.intersection(fabs)
            if found:
                self._request_reload(
                    f"appliance {', '.join(sorted(found))} answers at {ip}"
                )
                return

    def _request_reload(self, reason: str) -> None:
        if self._reload_requested:
            return
        last = self._hass.data.setdefault(DATA_LAST_RECOVERY_RELOAD, {})
        now = time.monotonic()
        since = now - last.get(self._entry_id, float("-inf"))
        if since < self._min_reload_gap:
            _LOGGER.info(
                "%s, but the integration reloaded for a missed appliance %.0f s "
                "ago — waiting for the next check", reason, since,
            )
            return
        self._reload_requested = True
        last[self._entry_id] = now
        _LOGGER.warning("%s — reloading Miele@LAN to set it up", reason)
        self._hass.config_entries.async_schedule_reload(self._entry_id)
