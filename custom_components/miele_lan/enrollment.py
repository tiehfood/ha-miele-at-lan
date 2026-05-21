"""Per-device enrollment for SuperVision push.

For each cloud-paired appliance in the household, we:
  1. Resolve its LAN IP (mDNS-discovered or stored config).
  2. Verify the household key still works with a signed GET /Devices.
  3. PUT /Devices/{fab}/SuperVision/{ha_fab} {"Show": true, "Signal": true}
     — flips the firmware's dispatch flag for our peer entry.
  4. POST /Subscriptions/ x4 for /State/, /State/Light/, /State/Status/, /Ident/
     — registers our hostname-based Callback URLs.

Idempotent: re-running cleanly on HA restart just overwrites existing slots.
Designed to be called once per device at integration startup, plus on
re-enrollment events (config change, refresh-token rotation, …).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from dataclasses import dataclass

import aiohttp

from .api import MieleLanClient

_LOGGER = logging.getLogger(__name__)

# Sub-resources we want the firmware to push to us. Order matters only for log
# clarity — registration order doesn't affect dispatch.
PUSH_RESOURCES: tuple[str, ...] = (
    "/State/",
    "/State/Light/",
    "/State/Status/",
    "/Ident/",
)


@dataclass(frozen=True)
class EnrolledDevice:
    """Result of an enrollment attempt — what worked, what didn't."""

    fab: str
    host_ip: str
    route: str  # asyncmiele's "route" is just the device's fab number here
    supervision_ok: bool        # PUT /SuperVision/{ha_fab} {Show,Signal} accepted
    subscriptions_ok: list[str]  # resources for which /Subscriptions/ returned 2xx
    enrolled_at: float           # epoch seconds when enrollment completed


def _miele_pad(body: bytes) -> bytes:
    if not body:
        return body
    if body[:1] == b"{" and body[-1:] == b"}" and len(body) < 64:
        return body[:-1] + b" " * (64 - len(body)) + b"}"
    rem = len(body) % 16
    if rem == 0 and len(body) >= 64:
        return body
    needed = max(64 - len(body), 0) or (16 - rem)
    return body + b" " * needed


async def _signed_subscription_post(
    session: aiohttp.ClientSession,
    *,
    host_ip: str,
    full_resource: str,
    callback_url: str,
    group_id_hex: str,
    group_key_hex: str,
) -> tuple[int, str]:
    """POST /Subscriptions to a peer — MieleH256-signed, padded body."""
    from asyncmiele.utils import crypto as _crypto  # type: ignore

    gid = bytes.fromhex(group_id_hex)
    gk = bytes.fromhex(group_key_hex)
    body = (
        "{\n"
        f'\t"Resource": "{full_resource}",\n'
        f'\t"Callback":"{callback_url}"\n'
        "}"
    ).encode("utf-8")
    padded = _miele_pad(body)
    date = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
    auth, iv = _crypto.build_auth_header(
        method="POST",
        host=host_ip,
        resource="/Subscriptions",
        date=date,
        group_id=gid,
        group_key=gk,
        content_type_header="application/vnd.miele.v1+json; charset=utf-8",
        body=padded,
    )
    encrypted = _crypto.encrypt_payload(padded, gk[:32], iv)
    headers = {
        "Accept": "application/vnd.miele.v1+json",
        "Date": date,
        "Authorization": auth,
        "Host": host_ip,
        "Content-Type": "application/vnd.miele.v1+json; charset=utf-8",
    }
    async with session.post(
        f"http://{host_ip}/Subscriptions",
        headers=headers,
        data=encrypted,
        timeout=aiohttp.ClientTimeout(total=10),
    ) as r:
        return r.status, r.headers.get("Location", "")


async def enroll_device(
    *,
    fab: str,
    host_ip: str,
    group_id_hex: str,
    group_key_hex: str,
    ha_fab: str,
    ha_hostname: str,
    ha_port: int,
) -> EnrolledDevice | None:
    """Run the full enrollment dance against one appliance.

    Returns None on any failure; logs the reason at WARNING level.
    Successful enrollment is logged at INFO.
    """
    sv_ok = False
    # 1. Verify the household key works with a signed read.
    try:
        async with MieleLanClient.from_hex(
            host_ip, group_id_hex, group_key_hex, fab, timeout=8.0
        ) as client:
            try:
                devices = await client.get_devices()
            except Exception as err:
                _LOGGER.warning(
                    "[%s @ %s] signed verify failed — key wrong, device offline, "
                    "or not cloud-paired: %s",
                    fab, host_ip, err,
                )
                return None
            if fab not in devices:
                _LOGGER.warning(
                    "[%s @ %s] device responded but fab not in /Devices listing (got %s)",
                    fab, host_ip, list(devices),
                )
                return None

            # 2. SuperVision peer entry — Show=true, Signal=true.
            sv_body = json.dumps({"Show": True, "Signal": True}).encode("utf-8")
            try:
                status, raw = await client.raw._request_bytes(
                    "PUT",
                    f"/Devices/{fab}/SuperVision/{ha_fab}",
                    body=sv_body,
                    allowed_status=(200, 201, 202, 204, 400, 404),
                )
                if 200 <= status < 300:
                    sv_ok = True
                    _LOGGER.info(
                        "[%s] SuperVision Show=true Signal=true accepted (%d)",
                        fab, status,
                    )
                elif status == 404:
                    _LOGGER.info(
                        "[%s] no /SuperVision endpoint (basic device) — falling back to /Subscriptions only",
                        fab,
                    )
                else:
                    _LOGGER.warning(
                        "[%s] SuperVision PUT returned %d — %s",
                        fab, status, raw[:200],
                    )
            except Exception as err:
                _LOGGER.warning("[%s] SuperVision PUT raised: %s", fab, err)
    except Exception as err:
        _LOGGER.warning("[%s @ %s] enrollment dance failed: %s", fab, host_ip, err)
        return None

    # 3. Subscriptions — POST /Subscriptions to register our callback URLs.
    async with aiohttp.ClientSession() as session:
        ok_resources: list[str] = []
        for res in PUSH_RESOURCES:
            full_resource = f"/Devices/{fab}{res}"
            callback = (
                f"http://{ha_hostname}:{ha_port}"
                f"/Devices/{ha_fab}/SuperVision/{fab}{res}"
            )
            try:
                status, location = await _signed_subscription_post(
                    session,
                    host_ip=host_ip,
                    full_resource=full_resource,
                    callback_url=callback,
                    group_id_hex=group_id_hex,
                    group_key_hex=group_key_hex,
                )
            except Exception as err:
                _LOGGER.warning("[%s] subscription POST for %s failed: %s", fab, res, err)
                continue
            if 200 <= status < 300:
                ok_resources.append(res)
                _LOGGER.debug(
                    "[%s] /Subscriptions for %s → %d (slot=%s)",
                    fab, res, status, location,
                )
            else:
                _LOGGER.warning("[%s] /Subscriptions for %s → %d", fab, res, status)

    if sv_ok and ok_resources:
        _LOGGER.info("[%s @ %s] enrolled (push-capable) — routes: %s",
                     fab, host_ip, ", ".join(ok_resources))
    elif ok_resources:
        _LOGGER.info("[%s @ %s] enrolled (subs only — may polling-fallback)", fab, host_ip)
    elif sv_ok:
        _LOGGER.info("[%s @ %s] enrolled (sv only, no subs)", fab, host_ip)
    else:
        _LOGGER.warning("[%s @ %s] enrolled with no push channels — polling only", fab, host_ip)
    # Always return — coordinator falls back to polling. Returning None would
    # hide the device from HA, which is worse than push-less.
    return EnrolledDevice(
        fab=fab,
        host_ip=host_ip,
        route=fab,
        supervision_ok=sv_ok,
        subscriptions_ok=ok_resources,
        enrolled_at=time.time(),
    )


async def enroll_all(
    devices: list[dict[str, str]],
    *,
    group_id_hex: str,
    group_key_hex: str,
    ha_fab: str,
    ha_hostname: str,
    ha_port: int,
    resolver: "DeviceIpResolver",
) -> list[EnrolledDevice]:
    """Enroll every cloud-listed device that we can reach on the LAN.

    Devices we can't resolve (offline, mDNS slow, different VLAN) are skipped
    with a WARNING; enrollment can be retried later when they come online.
    """
    enrolled: list[EnrolledDevice] = []
    for d in devices:
        fab = d.get("fabNr") or d.get("fab")
        if not fab:
            continue
        host_ip = await resolver.resolve(fab)
        if not host_ip:
            _LOGGER.warning(
                "could not resolve LAN IP for %s — will retry next refresh", fab
            )
            continue
        result = await enroll_device(
            fab=fab,
            host_ip=host_ip,
            group_id_hex=group_id_hex,
            group_key_hex=group_key_hex,
            ha_fab=ha_fab,
            ha_hostname=ha_hostname,
            ha_port=ha_port,
        )
        if result is not None:
            enrolled.append(result)
    return enrolled


class DeviceIpResolver:
    """Resolve a fab number to a LAN IP.

    Tries (in order): explicit cache → mDNS browse → static config map.
    Lives outside enroll_all() so the same resolver can be reused for
    re-enrollment without rebuilding the mDNS browser.
    """

    def __init__(
        self,
        *,
        static: dict[str, str] | None = None,
        mdns_timeout: float = 4.0,
    ) -> None:
        self._static = static or {}
        self._cache: dict[str, str] = {}
        self._mdns_timeout = mdns_timeout

    def remember(self, fab: str, ip: str) -> None:
        self._cache[fab] = ip

    async def resolve(self, fab: str) -> str | None:
        if fab in self._cache:
            return self._cache[fab]
        if fab in self._static:
            self._cache[fab] = self._static[fab]
            return self._static[fab]
        ip = await self._mdns_resolve(fab)
        if ip:
            self._cache[fab] = ip
        return ip

    async def _mdns_resolve(self, fab: str) -> str | None:
        """Browse `_mieleathome._tcp.local.` and find the entry whose hostname
        encodes this fab number (real Miele hostnames embed the WiFi MAC).

        We don't have a reverse mapping from fab → MAC, so we collect all
        Miele services on the LAN and match on a synthetic hostname pattern
        we derive from the fab. If that fails, we fall back to checking each
        candidate's /Ident response (not implemented yet — best-effort).
        """
        try:
            from zeroconf import IPVersion
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except ImportError:
            return None

        from .push_listener import synthetic_mac_hostname

        target_hostname = synthetic_mac_hostname(fab)
        found_ip: str | None = None
        zc = AsyncZeroconf(ip_version=IPVersion.V4Only)

        try:
            services: list[tuple[str, str, int]] = []

            def _on_service(zeroconf, service_type, name, state_change) -> None:  # type: ignore[no-untyped-def]
                pass  # we'll resolve by direct lookup below

            browser = AsyncServiceBrowser(
                zc.zeroconf, ["_mieleathome._tcp.local."], handlers=[_on_service]
            )
            await asyncio.sleep(self._mdns_timeout)
            await browser.async_cancel()

            for record in zc.zeroconf.cache.entries_with_name(target_hostname.lower()):
                addr = getattr(record, "address", None)
                if addr is not None:
                    found_ip = ".".join(str(b) for b in addr)
                    break
        finally:
            await zc.async_close()

        return found_ip


async def mdns_discover_household(
    group_id_hex: str,
    group_key_hex: str,
    *,
    timeout: float = 4.0,
    zeroconf: Any | None = None,
    exclude_ha_fab: str | None = None,
    exclude_ip: str | None = None,
) -> list[dict[str, Any]]:
    """Browse the LAN for `_mieleathome._tcp.local.` services whose `group=`
    TXT matches `group_id_hex`, then signed-GET `/Devices` on each to learn
    the device's fab number(s). Returns one dict per discovered fab:

        {"fabNr": "...", "deviceType": <int>, "deviceName": "",
         "host": "<ip>", "hostname": "Miele-<eui64>.local."}

    Real Miele appliances advertise as `Miele <model>._mieleathome._tcp.local.`
    (no fab in the service name), so we cannot extract fab from mDNS alone.
    The signed GET /Devices is the canonical source.
    """
    try:
        from zeroconf import IPVersion, ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError:
        _LOGGER.warning("zeroconf not installed — cannot discover Miele appliances")
        return []

    want_group = group_id_hex.upper()
    hits: dict[str, dict[str, Any]] = {}  # key = ip; value = mDNS hit
    owns_zc = zeroconf is None
    zc = zeroconf if zeroconf is not None else AsyncZeroconf(ip_version=IPVersion.V4Only)
    seen_lock = asyncio.Lock()
    pending: list[asyncio.Task] = []

    async def _resolve(service_type: str, name: str) -> None:
        info = await zc.async_get_service_info(service_type, name, timeout=2000)
        if info is None:
            return
        props = {
            (k.decode("ascii", errors="ignore") if isinstance(k, bytes) else k):
            (v.decode("utf-8", errors="ignore") if isinstance(v, bytes) else v)
            for k, v in (info.properties or {}).items()
        }
        group = (props.get("group") or "").upper()
        if group != want_group:
            return
        addrs = info.parsed_addresses()
        ip = addrs[0] if addrs else ""
        if not ip:
            return
        if exclude_ip and ip == exclude_ip:
            _LOGGER.debug("ignoring self-advertised mDNS hit at our own IP %s", ip)
            return
        # Skip our own listener: instance name contains "HomeAssistant <ha_fab>"
        # or the synthetic fab.
        if exclude_ha_fab and exclude_ha_fab in name:
            _LOGGER.debug("ignoring self-advertised mDNS hit (ha_fab=%s)", exclude_ha_fab)
            return
        async with seen_lock:
            if ip in hits:
                return
            hits[ip] = {
                "ip": ip,
                "hostname": (info.server or "").rstrip("."),
                "devicetype": int(props.get("devicetype", "0"))
                if (props.get("devicetype") or "").isdigit() else 0,
            }

    def _on_state(zeroconf, service_type, name, state_change):  # type: ignore[no-untyped-def]
        if state_change is ServiceStateChange.Added:
            pending.append(asyncio.create_task(_resolve(service_type, name)))

    browser = AsyncServiceBrowser(
        zc.zeroconf, ["_mieleathome._tcp.local."], handlers=[_on_state]
    )
    try:
        await asyncio.sleep(timeout)
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        await browser.async_cancel()
        if owns_zc:
            await zc.async_close()

    if not hits:
        _LOGGER.warning(
            "mDNS browse found no _mieleathome._tcp.local. services matching "
            "group=%s — HA on the same LAN as the appliances?",
            want_group,
        )
        return []

    _LOGGER.info(
        "mDNS found %d Miele service(s) matching household %s — probing for fabs",
        len(hits), want_group,
    )

    # Signed GET /Devices on each hit to learn the actual fab number(s).
    results: list[dict[str, Any]] = []
    for hit in hits.values():
        fabs = await _signed_get_fabs(
            ip=hit["ip"],
            group_id_hex=group_id_hex,
            group_key_hex=group_key_hex,
        )
        if not fabs:
            _LOGGER.warning(
                "[%s] discovered via mDNS but signed GET /Devices returned no fabs "
                "— wrong household key or device offline?", hit["ip"],
            )
            continue
        for fab in fabs:
            results.append({
                "fabNr": fab,
                "deviceType": hit["devicetype"],
                "deviceName": "",
                "host": hit["ip"],
                "hostname": hit["hostname"],
            })
            _LOGGER.info("[%s] fab %s confirmed", hit["ip"], fab)
    return results


async def _signed_get_fabs(
    *, ip: str, group_id_hex: str, group_key_hex: str
) -> list[str]:
    """Signed GET /Devices to learn the fab number(s) at this IP."""
    try:
        async with MieleLanClient.from_hex(
            ip, group_id_hex, group_key_hex, "_probe_", timeout=6.0,
        ) as client:
            devices = await client.get_devices()
            return [k for k in devices.keys() if k]
    except Exception as err:
        _LOGGER.debug("[%s] signed GET /Devices failed: %s", ip, err)
        return []


__all__ = [
    "EnrolledDevice",
    "DeviceIpResolver",
    "enroll_device",
    "enroll_all",
    "mdns_discover_household",
    "PUSH_RESOURCES",
]
