"""Miele@LAN coordinator — push-first with polling fallback.

Push lands via `push_listener.MielePushListener.on_push` and gets dispatched
here through `apply_push()`. Polling on a 60s cadence reconciles drift (lost
pushes, listener restarts, devices that don't enrol for some reason).

One coordinator per device; the integration spawns N coordinators per
household.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MieleLanClient
from .const import DOMAIN, OVEN_FAMILY, MieleAppliance
from .dop2 import parse_hours_of_operation
from .enrollment import EnrolledDevice
from .push_listener import PushEvent

_LOGGER = logging.getLogger(__name__)

POLL_FALLBACK_INTERVAL = 60  # seconds — slower than the active poll we used to do,
                             # because push handles the real-time work.
DOP2_REFRESH_INTERVAL = 600  # seconds — hours-of-operation barely changes; we
                             # only need to refresh ~once every 10 minutes.


@dataclass
class MieleLanData:
    """Last-known state for a single device. Mutable on each push/poll."""

    state: dict[str, Any] = field(default_factory=dict)
    ident: dict[str, Any] = field(default_factory=dict)
    wlan: dict[str, Any] = field(default_factory=dict)
    dop2: dict[str, Any] = field(default_factory=dict)


class MieleLanCoordinator(DataUpdateCoordinator[MieleLanData]):
    """Per-device coordinator.

    Two refresh paths:
      * `apply_push(event)` — synchronous, fires on each SuperVision push
      * `_async_update_data()` — polled fallback every POLL_FALLBACK_INTERVAL
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MieleLanClient,
        fab: str,
        enrollment: EnrolledDevice | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_{fab}",
            update_interval=timedelta(seconds=POLL_FALLBACK_INTERVAL),
        )
        self.client = client
        self.entry = entry
        self.fab = fab
        self.enrollment = enrollment
        self._data = MieleLanData()
        self._ident_loaded = False
        self._dop2_last_fetch: float = 0.0
        self._dop2_unsupported = False
        self._last_push_at: float | None = None
        self._push_count = 0

    # ------------------------------------------------------------- identity
    @property
    def device_type(self) -> MieleAppliance:
        """Best-effort MieleAppliance enum for this device.

        Sources, in order: cached value from ident, raw `DeviceType` string,
        or UNKNOWN as fallback. Stable across refreshes once ident loads.
        """
        ident = self._data.ident or {}
        raw = ident.get("device_type")
        if isinstance(raw, int):
            try:
                return MieleAppliance(raw)
            except ValueError:
                return MieleAppliance.UNKNOWN
        if isinstance(raw, str) and raw.isdigit():
            try:
                return MieleAppliance(int(raw))
            except ValueError:
                return MieleAppliance.UNKNOWN
        return MieleAppliance.UNKNOWN

    # --------- diagnostic helpers (used by the diagnostic sensor) ---------
    @property
    def last_push_at(self) -> float | None:
        return self._last_push_at

    @property
    def push_count(self) -> int:
        return self._push_count

    @property
    def push_mode(self) -> str:
        """Human-readable summary of how this device is currently fed.

        Values:
          "push:active"   — SV+subs ok AND we've received ≥1 push
          "push:ready"    — SV+subs ok but no push received yet
          "subs:ready"    — only /Subscriptions/ accepted; SV missing — uncertain
          "polling"       — no push channels — pure polling fallback
        """
        if self.enrollment is None:
            return "polling"
        sv = self.enrollment.supervision_ok
        subs = bool(self.enrollment.subscriptions_ok)
        if not sv and not subs:
            return "polling"
        if sv and subs:
            return "push:active" if self._push_count else "push:ready"
        if subs:
            return "push:active" if self._push_count else "subs:ready"
        return "polling"

    async def _async_update_data(self) -> MieleLanData:
        """Polled fallback: refresh /State and (once) /Ident + /WLAN."""
        try:
            state = await self.client.get_state()
            self._data.state = dict(state.raw_state)
            if not self._ident_loaded:
                self._data.ident = await self._fetch_full_ident()
                self._data.wlan = await self._fetch_wlan()
                self._ident_loaded = True
            await self._maybe_refresh_dop2()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err
        return self._data

    async def _maybe_refresh_dop2(self) -> None:
        """Refresh DOP2-sourced diagnostics for device types that expose them.

        Today: HoursOfOperation (leaf 2/119) for ovens. The leaf only updates
        when a program runs, so we throttle to DOP2_REFRESH_INTERVAL — much
        slower than the /State poll. Some appliances (and many cloud-only
        commissionings) return 404 here; we latch off after the first miss to
        avoid a steady noise of failures in the log.
        """
        if self._dop2_unsupported:
            return
        if self.device_type not in OVEN_FAMILY:
            return
        now = time.monotonic()
        if now - self._dop2_last_fetch < DOP2_REFRESH_INTERVAL:
            return
        self._dop2_last_fetch = now
        try:
            st, body = await self.client.raw._request_bytes(
                "GET", f"/Devices/{self.fab}/DOP2/2/119",
                allowed_status=(200, 403, 404),
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] DOP2 2/119 fetch failed: %s", self.fab, err)
            return
        if st != 200:
            self._dop2_unsupported = True
            _LOGGER.debug(
                "[%s] DOP2 2/119 returned %d — disabling DOP2 reads on this device",
                self.fab, st,
            )
            return
        try:
            self._data.dop2["hours_of_operation"] = parse_hours_of_operation(body)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] DOP2 2/119 parse failed: %s", self.fab, err)

    async def _fetch_wlan(self) -> dict[str, Any]:
        """Read `/WLAN/` once at first refresh. The device exposes its current
        WiFi config + RSSI/signal-strength here (no auth needed beyond the
        usual MieleH256 — host-scoped, not device-scoped).
        """
        import json as _json
        try:
            _, raw = await self.client.raw._request_bytes(
                "GET", "/WLAN/", allowed_status=(200,),
            )
            data = _json.loads(raw.decode("utf-8", errors="replace"))
            if isinstance(data, dict):
                # Don't keep the masked password
                data.pop("Key", None)
                return data
        except Exception as err:
            _LOGGER.debug("[%s] /WLAN/ fetch failed: %s", self.fab, err)
        return {}

    def apply_push(self, event: PushEvent) -> None:
        """Called by the push listener — merge a decoded push into our state."""
        if event.peer_fab != self.fab:
            _LOGGER.debug(
                "[%s] push fab mismatch — peer=%s — ignored",
                self.fab, event.peer_fab,
            )
            return
        # Resource as parsed from the encrypted body is the *peer's own* path
        # (e.g. "/Devices/000192453012/State/"), not the dispatch-side suffix.
        # Classify by substring rather than prefix.
        merged = False
        res = event.resource or ""
        is_ident = "/Ident" in res
        is_state = "/State" in res and not is_ident
        if is_state:
            if isinstance(event.content, dict) and event.content:
                before = dict(self._data.state)
                self._data.state = {**before, **event.content}
                changed = {
                    k: (before.get(k), v)
                    for k, v in event.content.items()
                    if before.get(k) != v
                }
                _LOGGER.info(
                    "[%s] push merged %d field(s) into /State; changed=%s",
                    self.fab, len(event.content), list(changed.keys()),
                )
                merged = True
            else:
                _LOGGER.warning(
                    "[%s] push for /State had empty/non-dict Content "
                    "(content_type=%s, raw_head=%s) — entities not updated",
                    self.fab, type(event.content).__name__,
                    event.raw_plain[:200] if event.raw_plain else "<none>",
                )
        elif is_ident:
            if isinstance(event.content, dict) and event.content:
                self._data.ident = {**self._data.ident, **event.content}
                merged = True
        else:
            _LOGGER.info(
                "[%s] push resource %r matched neither /State nor /Ident — skipping",
                self.fab, res,
            )
        if merged:
            self._push_count += 1
            self._last_push_at = time.time()
            self.async_set_updated_data(self._data)

    async def _fetch_full_ident(self) -> dict[str, Any]:
        """Read raw /Devices/{fab}/Ident and extract appliance + XKM fields."""
        import json as _json

        result: dict[str, Any] = {
            "tech_type": "",
            "fab_number": self.fab,
            "device_type": "",
            "device_name": "",
            "mat_number": "",
            "fab_index": "",
            "xkm_tech_type": "",
            "xkm_fab_number": "",
            "xkm_release_version": "",
        }
        try:
            _, raw = await self.client.raw._request_bytes(
                "GET", f"/Devices/{self.fab}/Ident", allowed_status=(200,)
            )
            data = _json.loads(raw.decode("utf-8", errors="replace"))
            dev_label = data.get("DeviceIdentLabel") or {}
            xkm_label = data.get("XKMIdentLabel") or {}
            result.update(
                {
                    "device_type": str(data.get("DeviceType", "")),
                    "device_name": data.get("DeviceName", ""),
                    "tech_type": dev_label.get("TechType", ""),
                    "fab_number": dev_label.get("FabNumber", self.fab),
                    "mat_number": dev_label.get("MatNumber", ""),
                    "fab_index": dev_label.get("FabIndex", ""),
                    "xkm_tech_type": xkm_label.get("TechType", ""),
                    "xkm_fab_number": xkm_label.get("FabNumber", ""),
                    "xkm_release_version": xkm_label.get("ReleaseVersion", ""),
                }
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] full ident fetch failed: %s", self.fab, err)
        return result


__all__ = ["MieleLanCoordinator", "MieleLanData"]
