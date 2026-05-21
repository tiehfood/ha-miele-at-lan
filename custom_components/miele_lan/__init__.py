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

import aiohttp
from homeassistant.components import zeroconf as ha_zeroconf
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant

from .api import MieleLanClient
from .cloud import refresh_access_token
from .const import (
    CONF_COUNTRY,
    CONF_DEVICES,
    CONF_GROUP_ID,
    CONF_GROUP_KEY,
    CONF_HA_FAB,
    CONF_HA_PORT,
    CONF_REFRESH_TOKEN,
    CONF_STATIC_IPS,
    CONF_ROUTE,
    DEFAULT_HA_PUSH_PORT,
    DOMAIN,
)
from .coordinator import MieleLanCoordinator
from .enrollment import DeviceIpResolver, enroll_all, mdns_discover_household
from .push_listener import (
    MielePushListener,
    PushEvent,
    detect_lan_ip,
    synthetic_mac_hostname,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LIGHT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Miele@LAN from a config entry. Both flow kinds (cloud OAuth and
    direct-keys with mDNS discovery) share the same runtime — one household
    entry, multi-device, push-first."""
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


async def _setup_cloud(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Cloud-pair path: household key already extracted, enrol every device,
    start the push listener, spawn one coordinator per device."""
    group_id: str = entry.data[CONF_GROUP_ID]
    group_key: str = entry.data[CONF_GROUP_KEY]
    devices: list[dict[str, Any]] = list(entry.data.get(CONF_DEVICES) or [])
    ha_fab: str = entry.data[CONF_HA_FAB]
    ha_port: int = entry.data.get(CONF_HA_PORT, DEFAULT_HA_PUSH_PORT)
    static_ips: dict[str, str] = entry.data.get(CONF_STATIC_IPS) or {}

    if not devices:
        _LOGGER.warning("cloud entry has no devices — nothing to set up")
        return False

    # HA's LAN-facing IP on the interface that routes toward the appliances.
    # If we know any appliance IP, route toward it (multi-homed boxes pick the
    # right interface). Otherwise just toward a public IP.
    route_target = next(iter(static_ips.values()), None) or "1.1.1.1"
    ha_lan_ip = detect_lan_ip(route_target)
    _LOGGER.info("HA LAN IP for mDNS / push listener: %s", ha_lan_ip)

    bundle: dict[str, Any] = {
        "kind": "cloud",
        "coordinators": {},
        "listener": None,
        "ha_fab": ha_fab,
        "clients": [],
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
    )
    mdns_static = {r["fabNr"]: r["host"] for r in mdns_results if r.get("fabNr")}
    if mdns_static:
        _LOGGER.info("mDNS resolved %d fab→IP mapping(s): %s",
                     len(mdns_static), mdns_static)
    merged_static_ips = {**static_ips, **mdns_static}
    if not devices and mdns_results:
        devices = [
            {"fabNr": r["fabNr"], "deviceType": r.get("deviceType", 0),
             "deviceName": ""}
            for r in mdns_results
        ]

    # Step 2: bring up the push listener (uses the same shared zeroconf).
    async def _on_push(event: PushEvent) -> None:
        coords = bundle["coordinators"]
        coord = coords.get(event.peer_fab)
        if coord is None:
            _LOGGER.warning(
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
    bundle["listener"] = listener

    resolver = DeviceIpResolver(static=merged_static_ips)
    enrolled = await enroll_all(
        devices,
        group_id_hex=group_id,
        group_key_hex=group_key,
        ha_fab=ha_fab,
        ha_hostname=listener.hostname,
        ha_port=ha_port,
        resolver=resolver,
    )

    for ed in enrolled:
        client = MieleLanClient.from_hex(
            host=ed.host_ip,
            group_id_hex=group_id,
            group_key_hex=group_key,
            route=ed.fab,
        )
        await client.__aenter__()
        bundle["clients"].append(client)
        coord = MieleLanCoordinator(hass, entry, client, fab=ed.fab, enrollment=ed)
        await coord.async_config_entry_first_refresh()
        bundle["coordinators"][ed.fab] = coord

    if not bundle["coordinators"]:
        await listener.stop()
        _LOGGER.error("no devices reachable — aborting setup")
        return False

    if entry.data.get(CONF_REFRESH_TOKEN):
        bundle["token_refresh_task"] = entry.async_create_background_task(
            hass, _token_refresh_loop(hass, entry), name=f"{DOMAIN}_token_refresh"
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = bundle
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _token_refresh_loop(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Refresh the access_token every ~50min; rotate refresh_token if Gigya does."""
    cc = entry.data[CONF_COUNTRY]
    while True:
        await asyncio.sleep(50 * 60)
        refresh = entry.data.get(CONF_REFRESH_TOKEN)
        if not refresh:
            return
        try:
            async with aiohttp.ClientSession() as session:
                tokens = await refresh_access_token(session, cc, refresh)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("token refresh failed: %s — retrying next cycle", err)
            continue
        new_refresh = tokens.get("refresh_token") or refresh
        if new_refresh != refresh:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: new_refresh}
            )
            _LOGGER.debug("refresh token rotated")


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    bundle = hass.data[DOMAIN].pop(entry.entry_id, None)
    if bundle is None:
        return True
    task = bundle.get("token_refresh_task")
    if task is not None:
        task.cancel()
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
    if entry.data.get("flow_kind") != "cloud":
        return
    group_id = entry.data.get(CONF_GROUP_ID)
    group_key = entry.data.get(CONF_GROUP_KEY)
    ha_fab = entry.data.get(CONF_HA_FAB)
    static_ips = entry.data.get(CONF_STATIC_IPS) or {}
    if not (group_id and group_key and ha_fab and static_ips):
        return
    import json as _json

    for fab, ip in static_ips.items():
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
