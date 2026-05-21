"""SuperVision push listener — HA-side implementation.

We pose as a household peer over mDNS (`_mieleathome._tcp.local.`) so cloud-paired
appliances dispatch their state changes to us in real time. The wire-level RE
is captured in [[supervision_protocol_full]] memory; this module is the
production version of `tools/miele_supervision_listener.py` with HA conventions:

  * no argparse / CLI / file logging
  * reuses HA's shared AsyncZeroconf when invoked from within HA
  * `start()`/`stop()` lifecycle
  * delivers decoded `PushEvent`s to a caller-provided async callback
  * binds an unprivileged port (firmware reads port from mDNS SRV; verified)

Identity defaults follow what we proved works for HA: `DeviceType=2147483647`
(panel-invisible), `DeviceName="Home Assistant"`, fab derived from the HA
config entry so re-installs keep the same identity.
"""

from __future__ import annotations

import asyncio
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiohttp import web

try:
    from zeroconf import IPVersion, ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf
except ImportError:  # pragma: no cover — handled at runtime
    IPVersion = ServiceInfo = AsyncZeroconf = None  # type: ignore

_LOGGER = logging.getLogger(__name__)

MIELE_OUI_EUI64 = "001D63FFFE"
DEFAULT_DEVICE_TYPE = 2147483647   # int.MaxValue → panel-invisible sentinel
DEFAULT_DEVICE_NAME = "Home Assistant"
DEFAULT_CON_STATE = 2              # 2 = Connected (mimic cloud-paired)
DEFAULT_COUNTRY_DOMAIN = "de_de"
MIELE_CONTENT_TYPE = "application/vnd.miele.v1+json; charset=utf-8"


@dataclass
class PushEvent:
    """One decoded SuperVision push from a peer appliance."""

    peer_fab: str                 # the device that pushed (e.g. "000192453012")
    resource: str                 # "/State/", "/State/Light/", "/State/Status/", "/Ident/"
    content: dict[str, Any]       # decoded JSON payload (the "Content" field)
    host_name: str | None         # mDNS hostname the peer advertised in its push body
    raw_plain: str                # full decoded plaintext (for diagnostics)


PushCallback = Callable[[PushEvent], Awaitable[None]]


def synthetic_mac_hostname(fab: str) -> str:
    """Derive `Miele-<EUI-64-hex>.local.` from a 12-digit fab number.

    Bottom 24 bits encode the fab so re-installations of the same HA instance
    keep the same hostname (the appliance side caches mDNS records).
    """
    digits = "".join(c for c in fab if c.isdigit())
    fab_int = int(digits[-8:] or "0")
    bottom24 = fab_int & 0xFFFFFF
    return f"Miele-{MIELE_OUI_EUI64}{bottom24:06X}.local."


def detect_lan_ip(target_ip: str) -> str:
    """Return the local IP that would route packets to `target_ip`."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target_ip, 80))
        return s.getsockname()[0]
    finally:
        s.close()


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


def _sign_and_encrypt(
    *,
    status: int,
    content_type: str,
    date: str,
    body_bytes: bytes,
    group_id: str,
    group_key: str,
) -> tuple[bytes, str]:
    """Sign + encrypt a response per Miele's MieleH256 protocol."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    gk = bytes.fromhex(group_key)
    padded = _miele_pad(body_bytes)
    canonical = f"{status}\n{content_type}\n{date}\n".encode("utf-8") + padded
    sig = hmac.new(gk, canonical, hashlib.sha256).digest()
    iv = sig[:16]
    if padded:
        aes_key = gk[: len(gk) // 2]
        enc = Cipher(algorithms.AES(aes_key), modes.CBC(iv), backend=default_backend()).encryptor()
        ciphertext = enc.update(padded) + enc.finalize()
    else:
        ciphertext = b""
    return ciphertext, f"MieleH256 {group_id.upper()}:{sig.hex().upper()}"


def _decrypt_push(group_key: str, sig_hex: str, body: bytes) -> str | None:
    """Decrypt an incoming push body. Returns plaintext or None on failure."""
    try:
        from asyncmiele.utils import crypto as _crypto  # type: ignore
    except ImportError:
        _LOGGER.warning("asyncmiele not installed — cannot decrypt push body")
        return None
    try:
        if len(sig_hex) % 2:
            sig_hex = "0" + sig_hex
        sig = binascii.a2b_hex(sig_hex)
        plain = _crypto.decrypt_response(body, sig, bytes.fromhex(group_key))
        return plain.decode("utf-8", errors="replace")
    except Exception as err:
        _LOGGER.debug("push decrypt failed: %s", err)
        return None


class MielePushListener:
    """HA-side SuperVision peer.

    Lifecycle:
        listener = MielePushListener(...)
        await listener.start()         # mDNS-advertises + binds HTTP
        ...
        await listener.stop()
    """

    def __init__(
        self,
        *,
        our_fab: str,
        group_id: str,
        group_key: str,
        host_ip: str,
        port: int,
        on_push: PushCallback,
        device_type: int = DEFAULT_DEVICE_TYPE,
        device_name: str = DEFAULT_DEVICE_NAME,
        country_domain: str = DEFAULT_COUNTRY_DOMAIN,
        zeroconf: AsyncZeroconf | None = None,
    ) -> None:
        self._our_fab = our_fab
        self._group_id = group_id.upper()
        self._group_key = group_key
        self._host_ip = host_ip
        self._port = port
        self._on_push = on_push
        self._device_type = device_type
        self._device_name = device_name
        self._country_domain = country_domain
        self._zc: AsyncZeroconf | None = zeroconf
        self._owns_zc = zeroconf is None
        self._service_info: ServiceInfo | None = None
        self._runner: web.AppRunner | None = None
        self._sub_slots: dict[str, int] = {}
        self._hostname = synthetic_mac_hostname(our_fab)

    @property
    def hostname(self) -> str:
        return self._hostname

    @property
    def our_fab(self) -> str:
        return self._our_fab

    async def start(self) -> None:
        if AsyncZeroconf is None:
            raise RuntimeError("zeroconf not installed — push listener cannot run")
        if self._zc is None:
            self._zc = AsyncZeroconf(ip_version=IPVersion.V4Only)
        await self._advertise_mdns()
        await self._start_http()
        _LOGGER.info(
            "Miele push listener started: %s on %s:%d (fab=%s)",
            self._hostname,
            self._host_ip,
            self._port,
            self._our_fab,
        )

    async def stop(self) -> None:
        if self._service_info is not None and self._zc is not None:
            try:
                await self._zc.async_unregister_service(self._service_info)
            except Exception as err:
                _LOGGER.debug("mDNS unregister failed: %s", err)
            self._service_info = None
        if self._owns_zc and self._zc is not None:
            await self._zc.async_close()
            self._zc = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        _LOGGER.info("Miele push listener stopped")

    async def _advertise_mdns(self) -> None:
        assert self._zc is not None
        desc = {
            "txtvers": "1",
            "group": self._group_id,
            "path": "/",
            "security": "1",
            "pairing": "false",
            "devicetype": str(self._device_type),
            "con": str(DEFAULT_CON_STATE),
            "subtype": "0",
            "s": "0",
            "cd": self._country_domain,
        }
        instance_name = f"HomeAssistant {self._our_fab}._mieleathome._tcp.local."
        self._service_info = ServiceInfo(
            "_mieleathome._tcp.local.",
            instance_name,
            addresses=[socket.inet_aton(self._host_ip)],
            port=self._port,
            properties=desc,
            server=self._hostname,
        )
        await self._zc.async_register_service(self._service_info, allow_name_change=True)
        _LOGGER.info(
            "mDNS advertised: %s server=%s addr=%s:%d group=%s "
            "devicetype=%s — appliances will reach us at this address+port",
            instance_name, self._hostname, self._host_ip, self._port,
            self._group_id, self._device_type,
        )

    async def _start_http(self) -> None:
        app = web.Application()
        app["push"] = self
        # Subscription-create POSTs from peers (they want us to push our state to them)
        app.router.add_post("/Subscriptions", _handle_subscription_create)
        app.router.add_post("/Subscriptions/", _handle_subscription_create)
        # SuperVision push delivery — root + the three sub-resources
        for suffix in ("State/", "State/Light/", "State/Status/", "Ident/"):
            app.router.add_post(
                rf"/Devices/{{ourfab:[0-9]+}}/SuperVision/{{peerfab:[0-9]+}}/{suffix}",
                _handle_supervision_push,
            )
        # Discovery handshake (real Miele devices probe these before enrolling us)
        app.router.add_get("/Devices", _handle_devices_root)
        app.router.add_get("/Devices/", _handle_devices_root)
        app.router.add_get(r"/Devices/{fab:[0-9]+}", _handle_device)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/", _handle_device)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/Ident", _handle_ident)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/Ident/", _handle_ident)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/State", _handle_state)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/State/", _handle_state)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/SuperVision", _handle_supervision_self)
        app.router.add_get(r"/Devices/{fab:[0-9]+}/SuperVision/", _handle_supervision_self)
        app.router.add_get(
            r"/Devices/{fab:[0-9]+}/SuperVision/{peerfab:[0-9]+}", _handle_supervision_peer
        )
        app.router.add_get(
            r"/Devices/{fab:[0-9]+}/SuperVision/{peerfab:[0-9]+}/", _handle_supervision_peer
        )
        # Catch-all (log + 204 so peers don't retry)
        app.router.add_route("*", "/{tail:.*}", _handle_catchall)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()

    # ------------------------------------------------------------------ build
    # Helpers used by handlers below; tucked into the class so we can access
    # group_id/group_key without app-state plumbing.

    def _signed_response(self, body_text: str) -> web.Response:
        date = dt.datetime.now(dt.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT")
        body, sig = _sign_and_encrypt(
            status=200,
            content_type=MIELE_CONTENT_TYPE,
            date=date,
            body_bytes=body_text.encode("utf-8"),
            group_id=self._group_id,
            group_key=self._group_key,
        )
        return web.Response(
            body=body,
            headers={
                "Content-Type": MIELE_CONTENT_TYPE,
                "Content-Length": str(len(body)),
                "Date": date,
                "X-Signature": sig,
                "Connection": "close",
            },
        )

    def _parse_auth(self, headers: dict[str, str]) -> tuple[str, str]:
        auth = headers.get("Authorization", "")
        if not auth.startswith("MieleH256 "):
            return "", ""
        rest = auth.split(" ", 1)[1]
        if ":" not in rest:
            return "", ""
        gid, sig = rest.split(":", 1)
        return gid, sig

    async def _emit(self, ev: PushEvent) -> None:
        try:
            await self._on_push(ev)
        except Exception:
            _LOGGER.exception("push callback raised — continuing")


# --------------------------------------------------------------------- handlers
# Free functions so aiohttp router doesn't need bound-method tricks. Each
# handler extracts the listener from `request.app["push"]`.


_RE_RESOURCE = re.compile(r'"Resource"\s*:\s*"([^"]+)"')


async def _handle_supervision_push(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    peer_fab = request.match_info.get("peerfab", "")
    path = request.path
    body = await request.read()
    headers = dict(request.headers)
    gid, sig = listener._parse_auth(headers)

    _LOGGER.debug(
        "push received from %s on %s (peer=%s, body_len=%d, sig=%s…)",
        request.remote, path, peer_fab, len(body), sig[:16],
    )

    plain = None
    if gid.lower() == listener._group_id.lower() and sig and body:
        plain = _decrypt_push(listener._group_key, sig, body)
    if plain is None:
        _LOGGER.warning(
            "push from %s on %s — decryption failed (group_id match=%s, sig_len=%d)",
            request.remote, path, gid.lower() == listener._group_id.lower(), len(sig),
        )

    content: dict[str, Any] = {}
    host_name: str | None = None
    resource = path.split(f"SuperVision/{peer_fab}", 1)[-1] or "/"
    if plain:
        try:
            parsed = json.loads(plain.strip().rstrip("\x20"))
        except json.JSONDecodeError:
            # Real bodies have trailing space padding; strip and retry
            stripped = plain.rstrip("\x20\r\n\t")
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = {}
        if isinstance(parsed, dict):
            content = parsed.get("Content") or {}
            host_name = parsed.get("Host")
            resource_from_body = parsed.get("Resource")
            if isinstance(resource_from_body, str):
                resource = resource_from_body

    if plain:
        _LOGGER.debug(
            "push parsed: resource=%s content_keys=%s",
            resource,
            list(content.keys()) if isinstance(content, dict) else "<not dict>",
        )

    if plain and isinstance(content, dict):
        await listener._emit(
            PushEvent(
                peer_fab=peer_fab,
                resource=resource,
                content=content,
                host_name=host_name,
                raw_plain=plain,
            )
        )
    else:
        _LOGGER.warning(
            "push from %s on %s — could not decode (sig=%s, body_len=%d, plain=%s)",
            request.remote, path, sig[:16], len(body), bool(plain),
        )
    return web.Response(status=204)


async def _handle_subscription_create(request: web.Request) -> web.Response:
    """A peer asks us to push our /Ident or /State to them — assign a slot."""
    listener: MielePushListener = request.app["push"]
    body = await request.read()
    headers = dict(request.headers)
    gid, sig = listener._parse_auth(headers)
    plain = None
    if gid.lower() == listener._group_id.lower() and sig and body:
        plain = _decrypt_push(listener._group_key, sig, body)
    resource = "?"
    if plain:
        m = _RE_RESOURCE.search(plain)
        if m:
            resource = m.group(1)
    slot = listener._sub_slots.get(resource)
    if slot is None:
        slot = len(listener._sub_slots) + 1
        listener._sub_slots[resource] = slot
    host = headers.get("Host", "")
    return web.Response(
        status=201,
        headers={"Location": f"http://{host}/Subscriptions/{slot}/", "Content-Length": "0"},
    )


async def _handle_devices_root(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        f'\t"{listener._our_fab}":{{"href":"{listener._our_fab}/",'
        f'\t\t"Group":"{listener._group_id}",\n'
        '\t\t"Pairing":false\n'
        "}\n"
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_device(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        '\t"DOP2":{"href":"DOP2/"},\n'
        '\t"Ident":{"href":"Ident/"},\n'
        '\t"State":{"href":"State/"},\n'
        '\t"Settings":{"href":"Settings/"},\n'
        '\t"SuperVision":{"href":"SuperVision/"}\n'
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_ident(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        f'\t"DeviceType":{listener._device_type},\n'
        '\t"SubType":0,\n'
        f'\t"DeviceName":"{listener._device_name}",\n'
        '\t"AppSupport":661,\n'
        '\t"FctSupport":7,\n'
        '\t"XKMSupport":7,\n'
        '\t"ProtocolVersion":4,\n'
        '\t"DeviceIdentLabel":\n'
        "\t{\n"
        '\t\t"Version":"E",\n'
        f'\t\t"FabNumber":"{listener._our_fab}",\n'
        '\t\t"FabIndex":"01",\n'
        '\t\t"TechType":"HALAN",\n'
        '\t\t"MatNumber":"00000001",\n'
        '\t\t"SWIDs":[1]\n'
        "\t},\n"
        '\t"XKMIdentLabel":\n'
        "\t{\n"
        '\t\t"Version":"E",\n'
        '\t\t"FabNumber":"000000000001",\n'
        '\t\t"FabIndex":"00",\n'
        '\t\t"TechType":"EK057S",\n'
        '\t\t"MatNumber":"00000000",\n'
        '\t\t"SWIDs":[1],\n'
        '\t\t"ReleaseVersion":"09.14"\n'
        "\t}\n"
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_state(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        '\t"Status":1,\n'
        '\t"InternalState":0,\n'
        '\t"ProgramType":0,\n'
        '\t"ProgramID":0,\n'
        '\t"ProgramPhase":0,\n'
        '\t"RemainingTime":[0,0],\n'
        '\t"StartTime":[0,0],\n'
        '\t"TargetTemperature":[-32768,-32768,-32768],\n'
        '\t"Temperature":[-32768,-32768,-32768],\n'
        '\t"SignalInfo":false,\n'
        '\t"SignalFailure":false,\n'
        '\t"SignalDoor":false,\n'
        '\t"RemoteEnable":[15,1,1],\n'
        '\t"ProcessAction":0,\n'
        '\t"DeviceAction":0,\n'
        '\t"Light":2,\n'
        '\t"StandbyState":1,\n'
        '\t"ElapsedTime":[0,0],\n'
        '\t"SyncState":1\n'
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_supervision_self(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        '\t"Enabled":true,\n'
        '\t"ErrorOnly":false\n'
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_supervision_peer(request: web.Request) -> web.Response:
    listener: MielePushListener = request.app["push"]
    body_text = (
        "{\n"
        '\t"Show":true,\n'
        '\t"Signal":true\n'
        "}\n"
    )
    return listener._signed_response(body_text)


async def _handle_catchall(request: web.Request) -> web.Response:
    _LOGGER.debug(
        "unexpected %s %s from %s", request.method, request.path_qs, request.remote
    )
    # 204 (not 404) — keeps peers from retrying aggressively.
    return web.Response(status=204)


__all__ = [
    "MielePushListener",
    "PushEvent",
    "PushCallback",
    "synthetic_mac_hostname",
    "detect_lan_ip",
]
