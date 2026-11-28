"""Miele@LAN — local Home Assistant integration.

Two setup paths share most plumbing:

* **Cloud-pair entry** (`flow_kind == "cloud"`): one entry per household, contains
  the cloud-issued GroupKey + the household devices list. We spin up a
  per-household push listener and one coordinator per device.

* **Manual entry** (`flow_kind == "manual"`): legacy single-device entry from
  the older locally-provisioned key flow. No push listener (polling-only).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import MieleLanClient
from .const import (
    CONF_ADVERTISE_ADDRESS,
    CONF_DEVICES,
    CONF_GROUP_ID,
    CONF_GROUP_KEY,
    CONF_HA_FAB,
    CONF_HA_PORT,
    CONF_STATIC_IPS,
    CONF_ROUTE,
    DATA_ENROLLED_HOSTS,
    DEFAULT_HA_PUSH_PORT,
    DOMAIN,
)
from .coordinator import MieleLanCoordinator
from .enrollment import (
    DeviceIpResolver,
    EnrolledDevice,
    _signed_get_fabs,
    cleanup_stale_subscriptions,
    enroll_all,
    mdns_discover_household,
    mdns_household_ids,
)
from .enrollment_report import (
    LEGACY_ISSUE_ID_PREFIX,
    FailedDevice,
    issue_id,
    issue_translation_placeholders,
    summary_line,
)
from .entity import model_label
from .recovery import MissedApplianceWatcher, note_setup_result
from .push_listener import (
    MielePushListener,
    PushEvent,
    detect_lan_ip,
    resolve_advertise_ip,
    synthetic_mac_hostname,
)
from .services import async_remove_services, async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.LIGHT,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Miele@LAN from a config entry. Both flow kinds (cloud OAuth and
    direct-keys with mDNS discovery) share the same runtime — one household
    entry, multi-device, push-first."""
    async_setup_services(hass)
    flow_kind = entry.data.get("flow_kind", "cloud")
    if flow_kind == "manual":
        # Legacy v1 single-device entries from pre-0.3.0 installs. Migrate
        # in-place to the new schema; the user can re-add via the menu.
        _LOGGER.warning(
            "config entry %s is in the old single-device schema — please "
            "remove and re-add via Settings → Devices & Services",
            entry.entry_id,
        )
        return False
    return await _setup_cloud(hass, entry)


def _merge_mdns_adoptions(
    devices: list[dict[str, Any]], mdns_results: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Union mDNS-confirmed fabs into the cloud worklist.

    A fab already present under either the `fabNr` or `fab` key (the two
    shapes `enroll_all` accepts) is not duplicated. Returns the merged
    worklist and the subset that was newly appended, in the same dict shape
    the enrollment worklist already uses.
    """
    known = {d.get("fabNr") or d.get("fab") for d in devices}
    known.discard(None)
    adopted = [
        {"fabNr": r["fabNr"], "deviceType": r.get("deviceType", 0), "deviceName": ""}
        for r in mdns_results
        if r.get("fabNr") and r["fabNr"] not in known
    ]
    return devices + adopted, adopted


def _push_unmatched_fab_action(
    expected_fabs: set[str], setup_complete: bool, fab: str,
) -> tuple[str, int]:
    """Decide how loudly to log a push whose fab has no coordinator yet.

    Enrollment creates a device's `/Subscriptions` entry (so the appliance
    starts pushing) before `_setup_cloud` spawns that device's coordinator —
    a push for a fab on our worklist during that window is an expected race,
    not an anomaly. Once setup has finished building every coordinator it
    expects to, a push with no coordinator is unexplained: a foreign
    appliance, or one that failed to enrol.
    """
    if not setup_complete and fab in expected_fabs:
        return "setup_in_progress", logging.DEBUG
    return "unknown", logging.WARNING


async def _setup_cloud(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Cloud-pair path: household key already extracted, enrol every device,
    start the push listener, spawn one coordinator per device."""
    group_id: str = entry.data[CONF_GROUP_ID]
    group_key: str = entry.data[CONF_GROUP_KEY]
    devices: list[dict[str, Any]] = list(entry.data.get(CONF_DEVICES) or [])
    ha_fab: str = entry.data[CONF_HA_FAB]
    ha_port: int = entry.data.get(CONF_HA_PORT, DEFAULT_HA_PUSH_PORT)
    # Bootstrap fab→IP map from the direct-keys setup blob, layered under
    # whatever the user has since set via the options flow (Settings →
    # Configure). mDNS results discovered below win over both on collision (a
    # stale cached IP from config-flow time shouldn't beat what the appliance
    # is announcing right now).
    static_ips: dict[str, str] = {
        **(entry.data.get(CONF_STATIC_IPS) or {}),
        **(entry.options.get(CONF_STATIC_IPS) or {}),
    }

    # HA's LAN-facing IP on the interface that routes toward the appliances.
    # If we know any appliance IP, route toward it (multi-homed boxes pick the
    # right interface). Otherwise just toward a public IP.
    route_target = next(iter(static_ips.values()), None) or "1.1.1.1"
    detected_ip = detect_lan_ip(route_target)
    ha_lan_ip, advertise_is_configured = resolve_advertise_ip(
        configured=entry.options.get(CONF_ADVERTISE_ADDRESS), detected=detected_ip,
    )
    _LOGGER.info(
        "push listener will advertise %s (%s) — this is also the address "
        "excluded from mDNS discovery to avoid self-adoption",
        ha_lan_ip,
        "configured via advertise address option" if advertise_is_configured else "auto-detected",
    )

    bundle: dict[str, Any] = {
        "kind": "cloud",
        "coordinators": {},
        "listener": None,
        "ha_fab": ha_fab,
        "clients": [],
        "setup_complete": False,
    }

    # Grab HA's shared zeroconf so we don't create competing instances.
    shared_zc = await ha_zeroconf.async_get_async_instance(hass)

    # Step 1: mDNS browse BEFORE starting the listener. Otherwise our own
    # listener would advertise with the household group and we'd "discover"
    # ourselves as a phantom appliance.
    _LOGGER.info(
        "browsing LAN for household %s (cloud devices listed: %d)",
        group_id, len(devices),
    )
    mdns_results = await mdns_discover_household(
        group_id_hex=group_id,
        group_key_hex=group_key,
        timeout=4.0,
        zeroconf=shared_zc,
        exclude_ha_fab=ha_fab,
        exclude_ip=ha_lan_ip,
        exclude_ip_is_configured=advertise_is_configured,
    )
    mdns_static = {r["fabNr"]: r["host"] for r in mdns_results if r.get("fabNr")}
    if mdns_static:
        _LOGGER.info("mDNS resolved %d fab→IP mapping(s): %s",
                     len(mdns_static), mdns_static)
    merged_static_ips = {**static_ips, **mdns_static}
    devices, adopted = _merge_mdns_adoptions(devices, mdns_results)
    if adopted:
        # The cloud device list is frozen at config-entry creation (issue #37)
        # and can be missing an appliance the cloud never returned — an empty
        # list on an `au` account whose appliances the Miele app shows fine,
        # or a partial list missing one of several appliances. mDNS is
        # authoritative for what is actually on the LAN, so union it in.
        _LOGGER.info(
            "mDNS confirmed %d appliance(s) not in the entry's device list "
            "(%s) — adopting for this setup run",
            len(adopted), ", ".join(d["fabNr"] for d in adopted),
        )

    if not devices:
        # Only now is this fatal: neither source knows of an appliance. Bailing
        # before the mDNS browse (as this used to) made the fallback above dead
        # code, since it is reachable only when the cloud list is empty.
        #
        # Distinguish "no Miele appliances here" from "appliances here, but in a
        # different household". They look identical in the log otherwise, and
        # the second one sends people hunting for an mDNS fault they don't have.
        lan_groups = await mdns_household_ids(timeout=4.0, zeroconf=shared_zc)
        others = sorted(lan_groups - {group_id.upper()})
        if others:
            _LOGGER.error(
                "household %s has no appliances, but this LAN has Miele "
                "appliance(s) in household %s. Your appliances are commissioned "
                "into a group your Miele account does not currently own, so the "
                "cloud cannot hand over its key. Re-pair them in the Miele app "
                "so they join the account's current household, or set the "
                "integration up with 'Paste household credentials' if you can "
                "obtain the GroupKey for %s.",
                group_id, ", ".join(others), others[0],
            )
        else:
            _LOGGER.warning(
                "household %s has no appliances — the cloud listed none and no "
                "Miele appliance answered mDNS on this LAN at all. Check HA "
                "shares a broadcast domain with the appliances (see the mDNS "
                "notes in the README).",
                group_id,
            )
        return False

    # Step 2: bring up the push listener (uses the same shared zeroconf).
    expected_fabs = {d.get("fabNr") or d.get("fab") for d in devices}
    expected_fabs.discard(None)

    async def _on_push(event: PushEvent) -> None:
        coords = bundle["coordinators"]
        coord = coords.get(event.peer_fab)
        if coord is None:
            action, level = _push_unmatched_fab_action(
                expected_fabs, bundle["setup_complete"], event.peer_fab,
            )
            if action == "setup_in_progress":
                _LOGGER.log(
                    level,
                    "push for fab %s ignored — setup still in progress, no "
                    "coordinator yet (expected: %s)",
                    event.peer_fab, sorted(expected_fabs),
                )
            else:
                watcher = bundle.get("recovery")
                if watcher is not None and watcher.notify_push(event.peer_fab):
                    return  # a missed appliance is back; the watcher reloads
                _LOGGER.log(
                    level,
                    "push for unknown fab %s — no coordinator (known: %s)",
                    event.peer_fab, list(coords.keys()),
                )
            return
        coord.apply_push(event)

    listener = MielePushListener(
        our_fab=ha_fab,
        group_id=group_id,
        group_key=group_key,
        host_ip=ha_lan_ip,
        port=ha_port,
        on_push=_on_push,
        zeroconf=shared_zc,
    )
    try:
        await listener.start()
    except OSError as err:
        _LOGGER.error(
            "could not bind push listener on port %d: %s "
            "(reconfigure the entry with a different ha_port)",
            ha_port, err,
        )
        return False
    except Exception as err:
        # NotRunningException from zeroconf, or any other startup fault —
        # release whatever the listener already grabbed and ask HA to retry.
        # Without this, the leaked mDNS registration / bound port poisons the
        # next setup attempt (issue #2).
        await _safe_stop_listener(listener)
        from zeroconf import NotRunningException  # local import — optional dep
        if isinstance(err, NotRunningException):
            raise ConfigEntryNotReady(
                "HA's shared zeroconf is not running yet — retrying setup"
            ) from err
        raise
    bundle["listener"] = listener

    resolver = DeviceIpResolver(static=merged_static_ips, zeroconf=shared_zc)
    try:
        enrollment_result = await enroll_all(
            devices,
            group_id_hex=group_id,
            group_key_hex=group_key,
            ha_fab=ha_fab,
            ha_hostname=listener.hostname,
            ha_port=ha_port,
            resolver=resolver,
            session=async_get_clientsession(hass),
        )
    except Exception:
        await _safe_stop_listener(listener)
        raise
    enrolled = enrollment_result.enrolled

    # Kept outside the entry's bundle on purpose: removing the entry unloads
    # it first, and async_remove_entry still needs to know where the
    # appliances are. mDNS households have no stored address otherwise.
    # Merged, not replaced: an appliance that is asleep during a reload still
    # holds the registration from the setup before.
    hass.data.setdefault(DATA_ENROLLED_HOSTS, {}).setdefault(entry.entry_id, {}).update(
        {ed.fab: ed.host_ip for ed in enrolled}
    )

    # Tidy up stale subscriptions from prior HA installs / fabs. The Miele
    # firmware never re-uses slot numbers, so leftover subs from an old
    # ha_fab keep accumulating until something deletes them. We sweep them
    # on every entry setup so reload-recovery is automatic.
    async def _cleanup(ed: EnrolledDevice) -> None:
        try:
            n = await cleanup_stale_subscriptions(
                host_ip=ed.host_ip, fab=ed.fab,
                group_id_hex=group_id, group_key_hex=group_key,
                our_ha_fab=ha_fab,
            )
            if n:
                _LOGGER.info("[%s] removed %d stale subscription slot(s) at setup", ed.fab, n)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("[%s] stale-sub cleanup at setup failed: %s", ed.fab, err)

    refresh_failed: list[FailedDevice] = []
    try:
        coords: dict[str, tuple[MieleLanCoordinator, MieleLanClient]] = {}
        for ed in enrolled:
            client = MieleLanClient.from_hex(
                host=ed.host_ip,
                group_id_hex=group_id,
                group_key_hex=group_key,
                route=ed.fab,
            )
            await client.__aenter__()
            bundle["clients"].append(client)
            coords[ed.fab] = (
                MieleLanCoordinator(hass, entry, client, fab=ed.fab, enrollment=ed),
                client,
            )

        async def _first_refresh(fab: str, coord: MieleLanCoordinator) -> None:
            await coord.async_config_entry_first_refresh()
            # Registered as soon as it has data, so pushes reach it right away.
            bundle["coordinators"][fab] = coord

        # One appliance failing its first refresh (a flaky dryer answering
        # 404 or timing out) used to fail the whole entry, taking every other
        # appliance down with it until Home Assistant's retry. It is now left
        # out like an appliance that could not be enrolled, and the recovery
        # watcher below brings it back. Only no working appliance at all is
        # still ConfigEntryNotReady (further down).
        results = await asyncio.gather(
            *(_first_refresh(fab, coord) for fab, (coord, _client) in coords.items()),
            return_exceptions=True,
        )
        for (fab, (_coord, client)), result in zip(coords.items(), results):
            if not isinstance(result, Exception):
                continue
            _LOGGER.warning("[%s] first refresh failed, leaving it out: %s", fab, result)
            # Its key worked a moment ago during enrollment, so this is the
            # appliance or its connection, not its setup.
            refresh_failed.append(
                FailedDevice(fab=fab, reason=f"first refresh failed: {result}", transient=True)
            )
            bundle["clients"].remove(client)
            try:
                await client.__aexit__(None, None, None)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("[%s] client close raised: %s", fab, err)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result  # cancellation and the like are not appliance faults
        # Keep the enrollment order, whatever order the refreshes finished in.
        bundle["coordinators"] = {
            fab: bundle["coordinators"][fab] for fab in coords if fab in bundle["coordinators"]
        }
    except Exception:
        # Any failure here would otherwise leak the listener and all
        # already-opened clients — see issue #2's NotRunningException loop.
        await _safe_stop_listener(listener)
        for client in bundle.get("clients", []):
            try: await client.__aexit__(None, None, None)
            except Exception: pass
        raise

    bundle["setup_complete"] = True

    if not bundle["coordinators"]:
        # No device reachable on the LAN yet. This is recoverable: an
        # appliance might be powered off, mDNS might be slow to populate,
        # multicast might be temporarily broken. Tear down what we started
        # so we don't leak the listener's mDNS registration + bound port,
        # then raise ConfigEntryNotReady so HA retries on its standard
        # backoff schedule. Users with permanently broken multicast can
        # configure static IPs via the options flow.
        await _safe_stop_listener(listener)
        for client in bundle.get("clients", []):
            try: await client.__aexit__(None, None, None)
            except Exception: pass
        raise ConfigEntryNotReady(
            "no Miele devices reachable on the LAN yet — will retry. "
            "Configure static IPs in the integration's options if your "
            "network blocks mDNS/multicast."
        )

    failed = enrollment_result.failed + refresh_failed
    line = summary_line(len(bundle["coordinators"]), failed)
    _LOGGER.log(logging.WARNING if failed else logging.INFO, line)
    # Earlier versions listed every appliance in one issue, which was deleted
    # and created again whenever that list changed.
    ir.async_delete_issue(hass, DOMAIN, f"{LEGACY_ISSUE_ID_PREFIX}{entry.entry_id}")
    device_types = {d.get("fabNr") or d.get("fab"): d.get("deviceType") for d in devices}
    for f in failed:
        name = registered_appliance_name(hass, f.fab)
        if f.transient and name is not None:
            # Set up before: its device stays, with its entities unavailable
            # until it answers, which is how Home Assistant shows a device it
            # cannot reach. There is nothing for the user to fix, so no issue;
            # one raised earlier is left as it is. The watcher brings it back.
            continue
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id(entry.entry_id, f.fab),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="appliance_unreachable" if f.transient else "appliance_not_set_up",
            translation_placeholders=issue_translation_placeholders(
                f, name or model_label(device_types.get(f.fab)),
            ),
        )
    # Only an appliance that set up is known to be fine again.
    for fab in bundle["coordinators"]:
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id, fab))

    bundle["options_unsub"] = entry.add_update_listener(_async_options_updated)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = bundle
    # Entities gated on ident fields (hood fan, hood filters, extractor speed)
    # are created once below and won't reappear until a reload — give a
    # transient first-refresh /Ident failure a bounded chance to heal first.
    await asyncio.gather(
        *(coord.async_ensure_ident_ready() for coord in bundle["coordinators"].values())
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The stale-subscription sweep only tidies up after other installs, and
    # nothing in setup depends on it, so it runs in the background instead of
    # making setup wait for the slowest appliance. Cancelled if the entry
    # unloads first. Appliances left out after a failed first refresh are
    # skipped: they just failed to answer, and the next setup sweeps them.
    async def _cleanup_all() -> None:
        await asyncio.gather(
            *(_cleanup(ed) for ed in enrolled if ed.fab in bundle["coordinators"])
        )

    entry.async_create_background_task(
        hass, _cleanup_all(), f"{DOMAIN} stale-subscription cleanup"
    )

    # Appliances setup could not enrol (off, asleep, flaky Wi-Fi) come back
    # through a reload once they are reachable, instead of staying unavailable
    # until the user reloads by hand.
    missing = expected_fabs - set(bundle["coordinators"])
    note_setup_result(hass, entry.entry_id, missing)
    if missing:
        # Where each missed appliance was last seen: configured statically,
        # found by this setup's mDNS browse, or enrolled by an earlier setup
        # (or by this one, for an appliance whose first refresh failed).
        known_addresses = {
            **hass.data.get(DATA_ENROLLED_HOSTS, {}).get(entry.entry_id, {}),
            **merged_static_ips,
        }
        watcher = MissedApplianceWatcher(
            hass=hass,
            entry_id=entry.entry_id,
            missing_fabs=missing,
            # Only working appliances: a missed one's announcements must count.
            enrolled_ips={
                ed.host_ip for ed in enrolled if ed.fab in bundle["coordinators"]
            },
            known_addresses={
                fab: ip for fab, ip in known_addresses.items() if fab in missing
            },
            group_id=group_id,
            exclude_ha_fab=ha_fab,
            probe=lambda ip: _signed_get_fabs(
                ip=ip, group_id_hex=group_id, group_key_hex=group_key,
            ),
            zeroconf=shared_zc,
            create_task=lambda coro, name: entry.async_create_background_task(
                hass, coro, name
            ),
        )
        bundle["recovery"] = watcher
        await watcher.async_start()
    return True


def registered_appliance_name(hass: HomeAssistant, fab: str) -> str | None:
    """The name Home Assistant shows for this appliance's device (the user's
    own name first), or None if it has never been set up here."""
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, fab)})
    if device is None:
        return None
    return device.name_by_user or device.name


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options flow changed the static-IP map — reload so it takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _safe_stop_listener(listener: MielePushListener) -> None:
    """Best-effort listener cleanup used by every failure path in _setup_cloud.

    A half-started listener still holds an mDNS registration + a bound port —
    if we leave it alive across a failed setup, HA's retry will collide with
    those and trip NotRunningException / OSError (issue #2).
    """
    try:
        await listener.stop()
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("listener stop on abort failed (ignored): %s", err)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    async_remove_services(hass)
    bundle = hass.data[DOMAIN].pop(entry.entry_id, None)
    if bundle is None:
        return True
    options_unsub = bundle.get("options_unsub")
    if options_unsub is not None:
        options_unsub()
    watcher = bundle.get("recovery")
    if watcher is not None:
        await watcher.async_stop()
    listener = bundle.get("listener")
    if listener is not None:
        await listener.stop()
    for client in bundle.get("clients", []):
        try:
            await client.__aexit__(None, None, None)
        except Exception:
            _LOGGER.debug("client close raised — ignoring during unload")
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Called when the user *removes* (not just unloads) the entry.

    We tidy up after ourselves by deleting our `/Subscriptions/` slots on
    every appliance and disabling our SuperVision peer entry. Best-effort —
    failures are logged but never block removal.
    """
    enrolled_hosts = hass.data.get(DATA_ENROLLED_HOSTS, {}).pop(entry.entry_id, {})
    ir.async_delete_issue(hass, DOMAIN, f"{LEGACY_ISSUE_ID_PREFIX}{entry.entry_id}")
    known_fabs = {
        d.get("fabNr") or d.get("fab") for d in entry.data.get(CONF_DEVICES) or []
    } | set(enrolled_hosts) | set(entry.options.get(CONF_STATIC_IPS) or {})
    for fab in sorted(f for f in known_fabs if f):
        ir.async_delete_issue(hass, DOMAIN, issue_id(entry.entry_id, fab))
    if entry.data.get("flow_kind") != "cloud":
        return
    group_id = entry.data.get(CONF_GROUP_ID)
    group_key = entry.data.get(CONF_GROUP_KEY)
    ha_fab = entry.data.get(CONF_HA_FAB)
    # Where the appliances were when this run last enrolled them wins over a
    # configured address; an entry that never loaded in this run only has
    # the configured ones.
    hosts = {
        **(entry.data.get(CONF_STATIC_IPS) or {}),
        **(entry.options.get(CONF_STATIC_IPS) or {}),
        **enrolled_hosts,
    }
    if not (group_id and group_key and ha_fab and hosts):
        return
    import json as _json

    for fab, ip in hosts.items():
        try:
            async with MieleLanClient.from_hex(
                ip, group_id, group_key, fab, timeout=6.0
            ) as c:
                # 1. Disable our SuperVision peer entry (idempotent on basic devices).
                try:
                    await c.raw._request_bytes(
                        "PUT", f"/Devices/{fab}/SuperVision/{ha_fab}",
                        body=_json.dumps({"Show": False, "Signal": False}).encode(),
                        allowed_status=(200, 201, 202, 204, 400, 404),
                    )
                except Exception:
                    pass
                # 2. Walk /Subscriptions/ and DELETE every slot whose Callback
                #    contains our ha_fab. We don't have to know slot numbers up
                #    front — the device assigns them.
                for n in range(1, 64):
                    try:
                        st, body = await c.raw._request_bytes(
                            "GET", f"/Subscriptions/{n}/",
                            allowed_status=(200, 404),
                        )
                    except Exception:
                        continue
                    if st != 200:
                        continue
                    txt = body.decode("utf-8", errors="replace")
                    if ha_fab in txt:
                        try:
                            await c.raw._request_bytes(
                                "DELETE", f"/Subscriptions/{n}/",
                                allowed_status=(200, 204, 404),
                            )
                            _LOGGER.info(
                                "[%s] removed subscription slot %d on entry removal",
                                fab, n,
                            )
                        except Exception:
                            pass
        except Exception as err:
            _LOGGER.warning(
                "[%s @ %s] subscription cleanup failed on entry removal: %s",
                fab, ip, err,
            )
