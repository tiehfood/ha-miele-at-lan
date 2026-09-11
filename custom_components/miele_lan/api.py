"""Thin facade over asyncmiele — fixes the bugs that broke writes on real devices.

What this module owns
---------------------
1. The MieleH256 PUT signing: device computes HMAC over the **padded** body, but
   upstream asyncmiele <=0.2.6 signs over the unpadded body and only pads at
   encrypt time → 403. We sign the padded body.
2. Action responses come back as JSON lists (e.g. ``[{"Success":{"DeviceAction":0}}]``).
   asyncmiele expects dicts → pydantic error. We unwrap when possible.
3. GLOBAL_USER_REQ is **not** the /State ``UserRequest`` field (washer-only,
   12141/12142). It's a binary write to DOP2 leaf ``2/1583``. We provide it here.

Everything else (crypto helpers, MieleClient class, response decryption) is
reused from asyncmiele.
"""

from __future__ import annotations

import asyncio
import binascii
import json
import logging
import struct
from typing import Any

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from asyncmiele import MieleClient
from asyncmiele.api import client as _client_mod
from asyncmiele.exceptions.network import (
    NetworkConnectionError,
    NetworkTimeoutError,
    ResponseError,
)
from asyncmiele.utils import crypto as _crypto
from asyncmiele.utils.http_consts import (
    ACCEPT_HEADER,
    CONTENT_TYPE_JSON,
    USER_AGENT,
)

from .const import (
    DEVICE_ACTION_WAKE,
    DISHWASHER_FAMILY,
    DOP1_LIGHTINGMODE_COOKING,
    DOP1_LIGHTINGMODE_OFF,
    DOP1_LUEFTERSTEUERUNG_OBJECT_ID,
    DOP1_NACHLAUFZEIT_OBJECT_ID,
    DOP1_REQUEST_TYPE_READ,
    DOP1_REQUEST_TYPE_WRITE,
    DOP1_SETTING_PF_OBJECT_ID,
    DOP1_SWITCHLIGHT_LICHTQUELLE_MAIN,
    DOP1_SWITCHLIGHT_OBJECT_ID,
    DOP1_SWITCHLIGHT_STRUKTUR_VERSION,
    LAUNDRY_FAMILY,
    OPCODE_LIGHT_OFF,
    OPCODE_LIGHT_ON,
    OPCODE_SWITCH_OFF,
    OPCODE_SWITCH_ON,
    PROCESS_ACTION_PAUSE,
    PROCESS_ACTION_START,
    PROCESS_ACTION_STOP,
    RUN_ON_TIME_MINUTES,
    STATUS_IN_USE,
    STATUS_PROGRAMMED,
    STATUS_WAITING_TO_START,
    USER_REQUEST_LEAF,
    USER_REQUEST_UNIT,
    MieleAppliance,
    parse_minutes_field,
)

_LOGGER = logging.getLogger(__name__)


# 16-byte prefix for DOP2 leaf 2/1583 writes. Decoded:
#   0010 (len=16)  0001 (struct id?)  062f (leaf=1583)  0000 (idx)
#   00 00 00 01 00 01 07 00  (struct header — field 1, type 0x07/e16)
_OVEN_REQ_PREFIX = bytes.fromhex("00100001062f00000000000100010700")
_OVEN_REQ_SUFFIX = b"\x20" * 15  # padding


def _pad_request_body(raw: bytes, blocksize: int = 16, json_min: int = 64) -> bytes:
    """Padding that satisfies both the MieleRESTServer convention and AES-CBC.

    asyncmiele's ``pad_payload`` returns JSON bodies >= 64 chars unchanged,
    which then blow up AES-CBC for non-16-aligned lengths. We always pad to
    the next 16-byte boundary, and additionally keep the ``>= 64-byte``
    minimum for JSON so the device behaves identically to the reference.
    """
    if not raw:
        return raw
    target = max(json_min if raw[:1] == b"{" and raw[-1:] == b"}" else 0, len(raw))
    if target % blocksize:
        target = target + (blocksize - (target % blocksize))
    if raw[:1] == b"{" and raw[-1:] == b"}":
        spaces = target - len(raw)
        if spaces > 0:
            return raw[:-1] + b" " * spaces + b"}"
        return raw
    if target == len(raw):
        return raw
    return raw + b"\x20" * (target - len(raw))


def build_user_request_payload(opcode: int) -> bytes:
    """Build the 32-byte DOP2 write payload for a GLOBAL_USER_REQ opcode."""
    if not 0 <= opcode <= 0xFF:
        raise ValueError(f"opcode out of range: {opcode}")
    return _OVEN_REQ_PREFIX + bytes([opcode]) + _OVEN_REQ_SUFFIX


def _check_dishwasher_precondition(state: dict[str, Any], action: int) -> str | None:
    """Dishwasher's own `RemoteEnable`/`Status` gate is native-only code we
    can't decompile, so we only refuse Start/Pause/Resume when `RemoteEnable[0]`
    is positively known to be 0. Stop is never refused locally — the app never
    gates it either.
    """
    if action == PROCESS_ACTION_STOP:
        return None
    remote_enable = state.get("RemoteEnable")
    if (
        isinstance(remote_enable, list)
        and remote_enable
        and isinstance(remote_enable[0], int)
        and remote_enable[0] == 0
    ):
        return f"Remote control is off on this appliance (RemoteEnable={remote_enable!r})."
    return None


def _check_laundry_precondition(state: dict[str, Any], action: int) -> str | None:
    """Laundry's exact `/State`-based gate (`UserRequestsDopSource` /
    `DeviceStateExtensions`). Pause/Resume don't exist on this family at all
    (no button is wired for them), so this only ever sees Start/Stop.

    Stop needs no `RemoteEnable` check whatsoever — only `Status` in
    {waiting_to_start, in_use}. Start additionally needs remote control on
    (`RemoteEnable[0]` bit 0), and while a programme is merely "programmed"
    (not yet waiting), needs mobile start on (`RemoteEnable[2]` bit 0) with
    no delayed start queued.
    """
    status = state.get("Status")
    if action == PROCESS_ACTION_STOP:
        if not isinstance(status, int) or status in (STATUS_WAITING_TO_START, STATUS_IN_USE):
            return None
        return (
            f"Stop is not available right now (Status={status}). Stop works while a "
            "programme is waiting to start or running (Status 4 or 5)."
        )
    if action != PROCESS_ACTION_START:
        return None

    remote_enable = state.get("RemoteEnable")
    remote0 = remote_enable[0] if isinstance(remote_enable, list) and remote_enable else None
    if isinstance(remote0, int) and not remote0 & 1:
        return f"Remote control is off on this appliance (RemoteEnable={remote_enable!r})."

    if not isinstance(status, int):
        return None
    if status == STATUS_WAITING_TO_START:
        return None
    if status == STATUS_PROGRAMMED:
        remote2 = (
            remote_enable[2]
            if isinstance(remote_enable, list) and len(remote_enable) > 2
            else None
        )
        mobile_start_known_off = isinstance(remote2, int) and not remote2 & 1
        minutes = parse_minutes_field(state.get("StartTime"))
        start_time_known_positive = isinstance(minutes, int) and minutes > 0
        if not mobile_start_known_off and not start_time_known_positive:
            return None

    return (
        f"Start is not available right now (Status={status}, RemoteEnable={remote_enable!r}). "
        "Start works when a programme is waiting to start (Status 4), or is programmed "
        "(Status 3) with Mobile start on and no delayed start."
    )


def check_process_action_precondition(
    state: dict[str, Any], action: int, device_type: MieleAppliance
) -> str | None:
    """Whether a cached `/State` rules out a `ProcessAction` write before we send it.

    Pure and I/O-free so it can be tested without a device. Returns ``None``
    when nothing known here rules the write out — that is *not* a guarantee
    of success: upstream has reports of Start being refused by the
    appliance's physical control panel with every `/State` field already
    correct, so a clean result only means "we have no local reason to
    expect a refusal". A non-``None`` result names the actual field value
    so the resulting error is never a guessed cause.

    Fields absent or not the expected type are treated as "unknown" and
    never block the write — we only refuse locally when we have positive
    evidence it will fail. The rule itself is per appliance family: laundry
    and the dishwasher expose very different gating over `/State` (see
    `_check_laundry_precondition` / `_check_dishwasher_precondition`), and
    ovens get no `ProcessAction` buttons at all.
    """
    if device_type in DISHWASHER_FAMILY:
        return _check_dishwasher_precondition(state, action)
    if device_type in LAUNDRY_FAMILY:
        return _check_laundry_precondition(state, action)
    return None


# --- Dop1 request builders --------------------------------------------------
# A Dop1 request is the hex string "{ObjectId}{RequestType}00{struct}", posted
# as {"Request": ...} to /Devices/{fab}/DOP/. Kept pure (no I/O) so the wire
# format can be tested without hardware — see tests/test_dop1_hood_payloads.py.
# Layouts are documented per-object in const.py.


def _dop1_request(object_id: str, request_type: str, section: bytes) -> str:
    return f"{object_id}{request_type}00{section.hex().upper()}"


def build_dop1_fan_level_request(level: int) -> str:
    """ServiceDataExt_Lueftersteuerung write — set the hood fan to `level`."""
    if not 0 <= level <= 5:
        raise ValueError(f"fan level out of range: {level}")
    section = bytes([0, 0xFF, level, 0])
    return _dop1_request(
        DOP1_LUEFTERSTEUERUNG_OBJECT_ID, DOP1_REQUEST_TYPE_WRITE, section
    )


def build_dop1_run_on_time_request(minutes: int) -> str:
    """ServiceDataExt_Nachlaufzeit write — set the hood fan run-on time."""
    if minutes not in RUN_ON_TIME_MINUTES:
        raise ValueError(
            f"run-on time must be one of {RUN_ON_TIME_MINUTES}: {minutes}"
        )
    return _dop1_request(
        DOP1_NACHLAUFZEIT_OBJECT_ID, DOP1_REQUEST_TYPE_WRITE, bytes([0, minutes])
    )


def build_dop1_main_light_request(on: bool) -> str:
    """SwitchLight_W write — switch the hood's main light on or off."""
    section = bytearray(13)
    section[0] = DOP1_SWITCHLIGHT_STRUKTUR_VERSION
    section[1] = DOP1_SWITCHLIGHT_LICHTQUELLE_MAIN
    section[2] = DOP1_LIGHTINGMODE_COOKING if on else DOP1_LIGHTINGMODE_OFF
    # Rot/Gruen/Blau_Dimmwert (offsets 3, 5, 7) stay 0.
    struct.pack_into(">H", section, 9, 0xFFFF if on else 0)  # WW_Dimmwert
    struct.pack_into(">H", section, 11, 0)  # KW_Dimmwert
    return _dop1_request(
        DOP1_SWITCHLIGHT_OBJECT_ID, DOP1_REQUEST_TYPE_WRITE, bytes(section)
    )


def build_dop1_setting_pf_write_request(pf_id: int, value: int) -> str:
    """Setting_PF_Schreiben_BE — write a Programmierfunktion value."""
    if not 0 <= pf_id <= 0xFFFF:
        raise ValueError(f"PF id out of range: {pf_id}")
    if not 0 <= value <= 0xFFFFFFFF:
        raise ValueError(f"PF value out of range: {value}")
    section = bytearray(7)
    struct.pack_into(">H", section, 1, pf_id)
    struct.pack_into(">I", section, 3, value)
    return _dop1_request(
        DOP1_SETTING_PF_OBJECT_ID, DOP1_REQUEST_TYPE_WRITE, bytes(section)
    )


def build_dop1_setting_pf_read_request(pf_id: int) -> str:
    """Setting_PF_Lesen_BE — ask for a Programmierfunktion's current value."""
    if not 0 <= pf_id <= 0xFFFF:
        raise ValueError(f"PF id out of range: {pf_id}")
    section = bytearray(3)
    struct.pack_into(">H", section, 1, pf_id)
    return _dop1_request(
        DOP1_SETTING_PF_OBJECT_ID, DOP1_REQUEST_TYPE_READ, bytes(section)
    )


def parse_dop1_setting_pf_value(response_hex: str, pf_id: int) -> int | None:
    """Pull the `Wert` (U32) out of a Setting_PF read response.

    The response carries a framing prefix (object id + message type) whose
    exact width isn't pinned down, so instead of assuming an offset we locate
    the echoed PF_ID and read the U32 that follows it. To avoid matching the
    same two bytes somewhere inside the framing, a candidate only counts if
    it sits on a byte boundary and the full 12 trailing bytes of the read
    section (Wert + Min + Max) still fit.

    Returns None when the value isn't locatable — callers treat that as
    "this appliance doesn't expose this setting".
    """
    digits = response_hex.strip().upper()
    if len(digits) % 2:  # not whole bytes — not something we can index into
        return None
    needle = f"{pf_id & 0xFFFF:04X}"
    start = 0
    while (idx := digits.find(needle, start)) != -1:
        if idx % 2 == 0 and idx + 4 + 24 <= len(digits):
            return int(digits[idx + 4 : idx + 12], 16)
        start = idx + 2
    return None


async def _patched_request_bytes(
    self: MieleClient,
    method: str,
    resource: str,
    *,
    body: bytes | str | dict[str, Any] | None = None,
    allowed_status: tuple[int, ...] = (200,),
) -> tuple[int, bytes]:
    """Drop-in replacement for MieleClient._request_bytes that signs the padded body.

    Returns (status_code, decrypted_bytes_or_raw).
    """
    method = method.upper()

    if body is None:
        raw_body: bytes = b""
    elif isinstance(body, bytes):
        raw_body = body
    elif isinstance(body, str):
        raw_body = body.encode("utf-8")
    else:
        raw_body = json.dumps(body).encode("utf-8")

    date_str = self._get_date_str()
    # Methods that carry a request body: PUT and POST. The official Miele cloud
    # uses POST with the same Content-Type + encrypted-body protocol as PUT
    # (verified via APK decompile of ISubscriptionApi).
    body_method = method in ("PUT", "POST")
    content_type_header = CONTENT_TYPE_JSON if body_method else ""

    if body_method and raw_body:
        signed_body = _pad_request_body(raw_body)
    else:
        signed_body = raw_body

    auth_header, iv = _crypto.build_auth_header(
        method=method,
        host=self.host,
        resource=resource,
        date=date_str,
        group_id=self.group_id,
        group_key=self.group_key,
        content_type_header=content_type_header,
        body=signed_body,
    )

    if body_method and signed_body:
        aes_key = self.group_key[: len(self.group_key) // 2]
        data_to_send: bytes | None = _crypto.encrypt_payload(signed_body, aes_key, iv)
    else:
        data_to_send = None

    headers = {
        "Accept": ACCEPT_HEADER,
        "User-Agent": USER_AGENT,
        "Host": self.host,
        "Date": date_str,
        "Authorization": auth_header,
    }
    if content_type_header:
        headers["Content-Type"] = content_type_header

    url = f"http://{self.host}{resource}"
    session = await self._get_session()

    try:
        async with session.request(
            method, url, data=data_to_send, headers=headers, timeout=self.timeout
        ) as resp:
            raw = await resp.read()
            status = resp.status

            if status not in allowed_status:
                raise ResponseError(status, f"API error for {resource}")
            if status == 204 or not raw:
                return status, b""

            sig_header = resp.headers.get("X-Signature")
            if not sig_header:
                return status, raw  # unencrypted error/text body

            sig_hex = sig_header.split(":", 1)[1]
            if len(sig_hex) % 2:
                sig_hex = "0" + sig_hex
            sig_bytes = binascii.a2b_hex(sig_hex)
            return status, _crypto.decrypt_response(raw, sig_bytes, self.group_key)
    except asyncio.TimeoutError as exc:
        raise NetworkTimeoutError(str(exc)) from exc
    except aiohttp.ClientConnectorError as exc:
        raise NetworkConnectionError(str(exc)) from exc


def _install_patches() -> None:
    """Monkey-patch asyncmiele's MieleClient with our PUT-signing fix.

    Idempotent — safe to call repeatedly.
    """
    if getattr(_client_mod.MieleClient._request_bytes, "_miele_lan_patched", False):
        return
    _patched_request_bytes._miele_lan_patched = True  # type: ignore[attr-defined]
    _client_mod.MieleClient._request_bytes = _patched_request_bytes  # type: ignore[assignment]


class MieleLanClient:
    """High-level local client for one provisioned Miele appliance.

    Wraps :class:`asyncmiele.MieleClient`. Use as an async context manager.

    >>> async with MieleLanClient.from_hex(host, group_id_hex, group_key_hex, route) as c:
    ...     state = await c.get_state()
    """

    def __init__(self, client: MieleClient, route: str) -> None:
        _install_patches()
        self._client = client
        self._route = route

    @classmethod
    def from_hex(
        cls,
        host: str,
        group_id_hex: str,
        group_key_hex: str,
        route: str,
        *,
        timeout: float = 10.0,
    ) -> "MieleLanClient":
        """Build a client from hex credentials and a known device route."""
        _install_patches()
        client = MieleClient.from_hex(host, group_id_hex, group_key_hex, timeout=timeout)
        return cls(client, route)

    async def __aenter__(self) -> "MieleLanClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self._client.__aexit__(*exc_info)

    @property
    def route(self) -> str:
        return self._route

    @property
    def raw(self) -> MieleClient:
        """Escape hatch to the underlying asyncmiele client for unwrapped calls."""
        return self._client

    # --- reads ---------------------------------------------------------------

    async def get_devices(self) -> dict[str, Any]:
        return await self._client.get_devices()

    async def get_ident(self) -> Any:
        return await self._client.get_device_ident(self._route)

    async def get_state(self) -> Any:
        return await self._client.get_device_state(self._route)

    async def read_dop2(self, unit: int, attr: int, idx1: int = 0, idx2: int = 0) -> bytes:
        """Raw DOP2 leaf read. Returns the decrypted body verbatim."""
        resource = f"/Devices/{self._route}/DOP2/{unit}/{attr}?idx1={idx1}&idx2={idx2}"
        status, raw = await self._client._request_bytes("GET", resource, allowed_status=(200,))
        if status != 200:
            raise ResponseError(status, f"DOP2 read {unit}/{attr} returned {status}")
        return raw

    # --- writes --------------------------------------------------------------

    async def _put_state(self, body: dict[str, Any]) -> dict[str, Any]:
        """PUT /State and parse the (list-wrapped) response.

        The device rejects no-op writes (writing the current value) with HTTP 400.
        We treat that as success — the caller's intent is satisfied.
        """
        try:
            status, raw = await self._client._request_bytes(
                "PUT",
                f"/Devices/{self._route}/State",
                body=body,
                allowed_status=(200, 204),
            )
        except ResponseError as e:
            if "HTTP error 400" in str(e):
                _LOGGER.debug("/State write %r returned 400 (likely no-op)", body)
                return {}
            raise
        if status == 204 or not raw:
            return {}
        text = raw.decode("utf-8", errors="replace").strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text}
        if isinstance(parsed, list):
            merged: dict[str, Any] = {}
            for item in parsed:
                if isinstance(item, dict):
                    merged.update(item)
            return merged
        return parsed if isinstance(parsed, dict) else {"raw": parsed}

    async def wake(self) -> dict[str, Any]:
        """Wake the appliance from sleep. Returns the device's action ack."""
        return await self._put_state({"DeviceAction": DEVICE_ACTION_WAKE})

    async def send_process_action(
        self,
        action: int,
        *,
        device_type: MieleAppliance | None = None,
        precondition_state: dict[str, Any] | None = None,
    ) -> None:
        """PUT /State {"ProcessAction": action} — start/stop/pause/resume a programme.

        This is the only control surface some appliances expose at all: a
        whole hardware/firmware family (EK057) answers HTTP 404 to every
        DOP2 leaf, including GLOBAL_USER_REQ (`write_user_request`), so
        `/State` is not a fallback here — it's the sole path for those
        devices. Confirmed against the reference implementation
        (MieleRESTServer), which performs remote start the same way.

        Unlike `_put_state` (used by `wake`/`light_on`/`light_off`), a
        refused process action is a real failure rather than a re-assertion
        of an already-current value, so HTTP 400 is not treated as success
        here — doing so previously produced a misleading "it worked" for a
        command the appliance actually rejected.

        `precondition_state` is the caller's last-known `/State` (no network
        round trip) and `device_type` selects which family's gate applies —
        when both are given, a write we already have positive evidence will
        fail is rejected locally with the actual field values named, instead
        of firing a request that's certain to be refused. See
        `check_process_action_precondition` for what "positive evidence"
        means and why a clean result is not a success guarantee.
        """
        if precondition_state is not None and device_type is not None:
            reason = check_process_action_precondition(precondition_state, action, device_type)
            if reason:
                raise HomeAssistantError(reason)
        try:
            await self._client._request_bytes(
                "PUT",
                f"/Devices/{self._route}/State",
                body={"ProcessAction": action},
                allowed_status=(200, 204),
            )
        except ResponseError as exc:
            raise HomeAssistantError(
                f"The appliance refused this command (HTTP {exc.status_code})."
            ) from exc

    async def start_process(
        self,
        *,
        device_type: MieleAppliance | None = None,
        precondition_state: dict[str, Any] | None = None,
    ) -> None:
        await self.send_process_action(
            PROCESS_ACTION_START, device_type=device_type, precondition_state=precondition_state
        )

    async def stop_process(
        self,
        *,
        device_type: MieleAppliance | None = None,
        precondition_state: dict[str, Any] | None = None,
    ) -> None:
        await self.send_process_action(
            PROCESS_ACTION_STOP, device_type=device_type, precondition_state=precondition_state
        )

    async def pause_process(
        self,
        *,
        device_type: MieleAppliance | None = None,
        precondition_state: dict[str, Any] | None = None,
    ) -> None:
        await self.send_process_action(
            PROCESS_ACTION_PAUSE, device_type=device_type, precondition_state=precondition_state
        )

    async def resume_process(
        self,
        *,
        device_type: MieleAppliance | None = None,
        precondition_state: dict[str, Any] | None = None,
    ) -> None:
        """Resume a paused programme.

        There is no Resume opcode — the app resumes by resending Start (1),
        and so do we.
        """
        await self.send_process_action(
            PROCESS_ACTION_START, device_type=device_type, precondition_state=precondition_state
        )

    async def write_user_request(self, opcode: int) -> None:
        """Send a GLOBAL_USER_REQ opcode via DOP2 leaf 2/1583.

        Works across oven, laundry, and dishwasher device classes.
        Some firmwares (e.g. EK057 FW 08.32) block all DOP2 writes
        unconditionally and return HTTP 404. A 403 means the appliance
        rejected the request as unauthorized (e.g. "Remote control" /
        "Mobile controllable" disabled in its settings menu). A 500 is the
        appliance failing to execute the command — it does not indicate a
        settings problem; the appliance may be in a state that doesn't accept
        this command right now, or this model may not support it locally.
        """
        payload = build_user_request_payload(opcode)
        resource = (
            f"/Devices/{self._route}/DOP2/"
            f"{USER_REQUEST_UNIT}/{USER_REQUEST_LEAF}?idx1=0&idx2=0"
        )
        try:
            await self._client._request_bytes(
                "PUT", resource, body=payload, allowed_status=(200, 204)
            )
        except ResponseError as exc:
            status = exc.status_code
            if status == 404:
                raise HomeAssistantError(
                    "This appliance's firmware does not accept remote commands "
                    "over the local API (DOP2 writes are blocked on this hardware/firmware)."
                ) from exc
            if status == 403:
                raise HomeAssistantError(
                    "Remote control was refused. Enable 'Remote control' / "
                    "'Mobile controllable' in the appliance's settings menu and try again."
                ) from exc
            if status == 500:
                raise HomeAssistantError(
                    "The appliance rejected the command (HTTP 500). This can happen "
                    "if it isn't in a state that accepts it right now, or if this "
                    "model doesn't support this command over the local API."
                ) from exc
            raise HomeAssistantError(
                f"Remote command failed (HTTP {status})."
            ) from exc

    # --- light / power convenience ------------------------------------------

    async def light_on(self) -> None:
        """Turn interior light on via the clean /State JSON API (no DOP2 needed)."""
        await self._put_state({"Light": 1})

    async def light_off(self) -> None:
        """Turn interior light off via the clean /State JSON API."""
        await self._put_state({"Light": 2})

    async def switch_on(self) -> None:
        await self.write_user_request(OPCODE_SWITCH_ON)

    async def switch_off(self) -> None:
        await self.write_user_request(OPCODE_SWITCH_OFF)

    # --- legacy Dop1 writes (hood ventilation / light / settings) -----------

    async def _dop1_post(self, request: str, *, what: str) -> bytes:
        """POST a signed Dop1 request to /DOP/ and return the decrypted body.

        HTTP 400 is the firmware's way of rejecting a no-op (re-asserting the
        value the appliance already holds), the same convention `_put_state`
        already tolerates for /State writes. An automation re-sending the
        current fan level shouldn't surface as an error, so treat it as
        success with an empty body.
        """
        try:
            _, raw = await self._client._request_bytes(
                "POST",
                f"/Devices/{self._route}/DOP/",
                body={"Request": request},
                allowed_status=(200, 204),
            )
            return raw or b""
        except ResponseError as exc:
            if exc.status_code == 400:
                _LOGGER.debug("%s: Dop1 %r returned 400 (likely no-op)", what, request)
                return b""
            raise HomeAssistantError(
                f"{what} failed (HTTP {exc.status_code})."
            ) from exc

    async def set_fan_level(self, level: int) -> None:
        """Set the hood's ventilation level (0 = off, 1-3, 4 = boost).

        Hood-only, and only on ProtocolVersion==2 appliances — the same gate
        the official app applies (see const.py). Returns as soon as the
        appliance acks: turning on takes a few seconds to show up in
        /State.VentilationStep while the motor spins up, and the push channel
        delivers that update when it lands.
        """
        await self._dop1_post(
            build_dop1_fan_level_request(level), what="Fan level write"
        )

    async def set_fan_run_on_time(self, minutes: int) -> None:
        """Set the hood fan's run-on time (Nachlaufzeit) in minutes."""
        await self._dop1_post(
            build_dop1_run_on_time_request(minutes), what="Fan run-on time write"
        )

    async def set_main_light_dop1(self, on: bool) -> None:
        """Switch the hood's main light via Dop1 SwitchLight_W.

        Same result as `light_on()`/`light_off()` but much faster to apply on
        hood firmware; callers keep the /State path as a fallback.
        """
        await self._dop1_post(
            build_dop1_main_light_request(on), what="Main-light write"
        )

    async def write_setting_pf(self, pf_id: int, value: int) -> None:
        """Write a Programmierfunktion setting via the generic Dop1 1201 object."""
        await self._dop1_post(
            build_dop1_setting_pf_write_request(pf_id, value),
            what=f"Setting {pf_id} write",
        )

    async def read_setting_pf(self, pf_id: int) -> int | None:
        """Read a Programmierfunktion setting's current value.

        Returns None when the appliance doesn't answer with a value for this
        setting, which is how callers detect an unsupported one.
        """
        raw = await self._dop1_post(
            build_dop1_setting_pf_read_request(pf_id), what=f"Setting {pf_id} read"
        )
        if not raw:
            return None
        # The device answers with JSON {"Response": "<hex>"}; fall back to
        # treating the body itself as the section if that shape ever changes.
        try:
            parsed = json.loads(raw.decode("utf-8", errors="replace"))
            response_hex = parsed.get("Response", "") if isinstance(parsed, dict) else ""
        except json.JSONDecodeError:
            response_hex = raw.hex()
        _LOGGER.debug("setting %d read response: %s", pf_id, response_hex)
        return parse_dop1_setting_pf_value(response_hex, pf_id)

    # Cooling-family target-temperature writes are not supported via the LAN
    # protocol — see custom_components/miele_lan/climate.py for the RE notes.
    # The climate entity is read-only by design.
