"""Miele@LAN — appliance provisioning CLI (one-shot, laptop-side).

Walks the user through the factory-state pairing flow that the Miele app
normally drives:

  1. (manual) connect laptop to the appliance SoftAP ("Miele@home" / "Miele@home-…")
  2. PUT /WLAN  — push the user's home-network SSID / PSK
     The device drops the SoftAP and joins the new WiFi.
  3. (wait) the device reappears on the home network with a DHCP-assigned IP.
  4. PUT /Security/Commissioning/  — push a freshly-generated GroupID + GroupKey.
     This is the **commissioning window**; after this call the device locks
     down `/Security/*` and `/Subscriptions` to read-only at our auth tier.
  5. (optional) POST /Subscriptions  — register an HA callback URL while we
     still have elevated rights. Skipped if --no-subscribe.
  6. Verify by signing `GET /Devices` with the new keys (asyncmiele path).
  7. Emit a paste-into-HA setup blob (base64 JSON) and a .claude/research/keys.yaml
     append-block.

Steps 2 and 4 use *unauthenticated* HTTPS/HTTP PUTs — same as MieleRESTServer's
`provision-wifi.sh` and `provision-key.sh`. We try HTTP first, then HTTPS with
`verify=False` (the device serves a self-signed cert at this stage).

Run examples:
  virtenv/bin/python tools/miele_lan_provision.py wifi --device 192.168.1.1 \\
      --ssid YourWiFi --psk '<your-wifi-password>' --sec WPA2
  virtenv/bin/python tools/miele_lan_provision.py commission \\
      --device <appliance-lan-ip> --out keys.yaml --endpoint oven
  virtenv/bin/python tools/miele_lan_provision.py verify \\
      --device <appliance-lan-ip> --keys keys.yaml --endpoint oven

Authoritative protocol references:
  - akappner/MieleRESTServer helpers/{provision-wifi.sh, provision-key.sh}
  - droman42/asyncmiele utils/provisioning.py (MieleSetupClient flow)
  - Live RE notes in .claude/ARCHITECTURE.md and memory/twin_device_feasibility.md
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import json
import secrets
import socket
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml

# Imported by the subscription retry block
ssl_default = ssl  # noqa: F841

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DEVICE_IP_SOFTAP = "192.168.1.1"
USER_AGENT = "Miele@mobile 2.3.3 iOS"  # matches MieleRESTServer

# Two content types in use, *verified* from InfoControl APK decompile:
#   - WiFi step uses Miele's odd-spaced MIME (matches MieleRESTServer source).
#   - Commissioning + TAN steps use plain application/json.
CT_MIELE = "application / vnd.miele.v1 + json; charset = utf - 8"
CT_ACCEPT_MIELE = "application/vnd.miele.v1 + json"
CT_JSON = "application/json"


# ---------------------------------------------------------------------------
# Key generation
# ---------------------------------------------------------------------------

def generate_keys() -> tuple[str, str]:
    """Generate fresh GroupID/GroupKey per the MieleRESTServer convention."""
    group_id = secrets.token_hex(8).upper()      # 16 hex chars = 8 bytes
    group_key = secrets.token_hex(64).upper()    # 128 hex chars = 64 bytes
    return group_id, group_key


def redact(value: str) -> str:
    if not value: return value
    return f"{value[:4]}…{value[-4:]} ({len(value)} chars)"


# ---------------------------------------------------------------------------
# Unauthenticated PUT — used during the commissioning window
# ---------------------------------------------------------------------------

async def _put_unauthenticated(
    session: aiohttp.ClientSession,
    device_ip: str,
    path: str,
    payload: bytes,
    *,
    try_https_first: bool = False,
    content_type: str = CT_JSON,
    accept: str = CT_JSON,
    method: str = "PUT",
    pairing_auth: bool = False,
) -> tuple[int, bytes]:
    """Try HTTP and HTTPS; return first (status, body) we get back.

    Per APK decompile + live experimentation 2026-05-19:
    - WiFi step (`/WLAN/`) is HTTPS on the SoftAP with the Miele MIME and
      no auth header.
    - Commissioning (`/Security/Commissioning/`) requires the magic
      header `Authorization: MielePairing:Pairing` (or any "Pairing"-bearing
      variant). Without it the device returns 404 to hide the path. With it
      the device accepts the keys and skips cloud-TAN validation entirely.
    - This `MielePairing:Pairing` auth lives across HTTP+HTTPS and across
      all Content-Type variants. The Miele app uses HTTPS + plain JSON.
    """
    headers = {
        "Content-Type": content_type,
        "Accept": accept,
        "User-Agent": USER_AGENT,
    }
    if pairing_auth:
        headers["Authorization"] = "MielePairing:Pairing"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    schemes = ["https", "http"] if try_https_first else ["http", "https"]
    last_status, last_body = 0, b""
    for scheme in schemes:
        kw = {"ssl": ssl_ctx} if scheme == "https" else {}
        url = f"{scheme}://{device_ip}{path}"
        try:
            async with session.request(method, url, data=payload, headers=headers, **kw) as resp:
                body = await resp.read()
                print(f"  [{scheme}] {method} {url}  →  HTTP {resp.status}  body_len={len(body)}")
                if body and len(body) < 400:
                    try: print(f"     body: {body.decode('utf-8', errors='replace').strip()[:300]}")
                    except Exception: pass
                last_status, last_body = resp.status, body
                if 200 <= resp.status < 300:
                    return resp.status, body
        except Exception as e:
            print(f"  [{scheme}] {url}  →  {type(e).__name__}: {str(e)[:120]}")
    return last_status, last_body


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def cmd_wifi(args: argparse.Namespace) -> int:
    """Push the user's home-network credentials to a factory-state appliance.

    Pre-req: laptop is joined to the appliance's SoftAP and reachable at
    --device (default 192.168.1.1). Field names + CT verified from APK decompile
    (`ConnectWlanRequestDtoBody`: JsonProperty SSID / Sec / Key).
    """
    body = {"SSID": args.ssid, "Sec": args.sec, "Key": args.psk}
    payload = json.dumps(body).encode("utf-8")
    print(f"PUT /WLAN/  body={ {**body, 'Key': '***'} }\n")
    timeout = aiohttp.ClientTimeout(total=12)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        status, _ = await _put_unauthenticated(
            session, args.device, "/WLAN/", payload,
            try_https_first=True,
            content_type=CT_MIELE, accept=CT_ACCEPT_MIELE,
        )
    if 200 <= status < 300:
        print("\n✓ WiFi credentials accepted. The appliance will drop the SoftAP and join the new WiFi.")
        print("  Watch your router for a new lease, then re-run this tool with `commission`.")
        return 0
    print(f"\n✗ FAILED with HTTP {status}.")
    return 1


async def cmd_commission(args: argparse.Namespace) -> int:
    """Push generated GroupID/GroupKey to the appliance (factory state, post-WiFi).

    Pre-req: appliance is on the home WiFi with a known IP (--device).
    """
    if args.group_id and args.group_key:
        group_id, group_key = args.group_id.upper(), args.group_key.upper()
        if len(group_id) != 16 or len(group_key) != 128:
            print(f"✗ --group-id must be 16 hex chars and --group-key 128 hex chars")
            return 1
        print(f"Using supplied keys (KEEP SECRET):")
    else:
        group_id, group_key = generate_keys()
        print(f"Generated keys (KEEP SECRET):")
    print(f"  GroupID  = {redact(group_id)} (full value will be saved to {args.out})")
    print(f"  GroupKey = {redact(group_key)}\n")

    # Body shape per APK decompile (`LocalGroupKeyIdDto`: JsonProperty GroupKey / GroupID).
    # Verified field names: "GroupKey" + "GroupID" (uppercase ID, NOT GroupId).
    # Content-Type per decompile: plain "application/json" (NOT Miele MIME).
    body = {"GroupKey": group_key, "GroupID": group_id}
    payload = json.dumps(body).encode("utf-8")
    if args.enable_only:
        print("--enable-only set: skipping unauthenticated PUT, going straight to signed EnableApplianceGroupMode\n")
        status = 200
    else:
        print(f"PUT /Security/Commissioning/  body_len={len(payload)}  (with MielePairing:Pairing header)\n")
        timeout = aiohttp.ClientTimeout(total=12)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            status, response_body = await _put_unauthenticated(
                session, args.device, "/Security/Commissioning/", payload,
                content_type=CT_JSON, accept=CT_JSON, pairing_auth=True,
            )

        if not (200 <= status < 300):
            print(f"\n✗ Commissioning FAILED with HTTP {status}.")
            if status == 403:
                print("  HTTP 403 = device already has these keys. Continuing to EnableApplianceGroupMode step.")
                print("  (use --enable-only to skip this PUT entirely on subsequent runs)")
            else:
                return 1
    print("\n✓ Commissioning accepted (or already in place). Saving keys.")

    # Detect device route by signed GET /Devices (best-effort — needs asyncmiele installed)
    route = await _detect_route(args.device, group_id, group_key) if not args.no_verify else None
    if route:
        print(f"✓ Detected device route: {route}")

    # EnableApplianceGroupMode — SIGNED PUT to /Security/Commissioning/ with the
    # same body. The Miele app does this immediately after the unauthenticated
    # commissioning PUT. Without this second call the panel stays on "Warten auf
    # App" (Status=12 / Service). Source: APK decompile, MieleJsonUtf8.cs:11755.
    if route:
        ok = await _enable_group_mode(args.device, group_id, group_key, route, payload)
        if not ok:
            print("  WARN: EnableApplianceGroupMode failed — panel may stay on 'Warten auf App'")

    # Optional step — register an HA callback subscription while still in the
    # commissioning window. Tested hypothesis: the device grants elevated rights
    # briefly after commissioning succeeds; POST /Subscriptions during this
    # window has a chance of being accepted, where the same call later returns 403.
    if args.register_subscription and route:
        await _try_register_subscription(args.device, group_id, group_key, route,
                                          args.register_subscription)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    block: dict[str, Any] = {
        "endpoints": {
            args.endpoint: {
                "host": args.device,
                "groupId": group_id,
                "groupKey": group_key,
                **({"route": route} if route else {}),
            }
        }
    }
    if out.exists() and out.stat().st_size:
        existing = yaml.safe_load(out.read_text()) or {}
        existing.setdefault("endpoints", {}).update(block["endpoints"])
        block = existing
    out.write_text(yaml.safe_dump(block, sort_keys=False))
    print(f"✓ Wrote {out.relative_to(ROOT)}")

    # Setup blob for paste-into-HA config flow
    blob_payload = {"host": args.device, "groupId": group_id, "groupKey": group_key, "route": route}
    blob = base64.b64encode(json.dumps(blob_payload, separators=(",", ":")).encode()).decode()
    print(f"\nHA setup blob (paste into the config flow's 'setup blob' field):")
    print(f"  {blob}")
    return 0


async def cmd_verify(args: argparse.Namespace) -> int:
    """Verify the device responds to MieleH256-signed reads with the new keys."""
    cfg = yaml.safe_load((ROOT / args.keys).read_text())["endpoints"][args.endpoint]
    print(f"Verifying {args.endpoint} @ {cfg['host']} with stored keys")
    sys.path.insert(0, str(ROOT))
    from custom_components.miele_lan.api import MieleLanClient  # noqa: E402
    try:
        async with MieleLanClient.from_hex(
            cfg["host"], cfg["groupId"], cfg["groupKey"], cfg.get("route", ""), timeout=8.0,
        ) as c:
            devices = await c.get_devices()
            print(f"✓ GET /Devices succeeded — routes: {list(devices)}")
            if cfg.get("route") and cfg["route"] in devices:
                ident = await c.get_ident()
                print(f"✓ Ident: {getattr(ident, 'tech_type', '?')}  route={cfg['route']}")
            return 0
    except Exception as e:
        print(f"✗ Verification failed: {type(e).__name__}: {e}")
        return 1


async def _try_register_subscription(
    device: str, group_id: str, group_key: str, route: str, callback_url: str,
) -> None:
    """Attempt POST /Subscriptions inside the commissioning window.

    Untested in the wild — on our already-commissioned oven this returns 403.
    The hypothesis is that immediately after PUT /Security/Commissioning/
    succeeds, the device retains an elevated principal for a brief moment,
    during which subscription creation is allowed. If your factory-reset
    device accepts this, the integration gets push notifications.

    Auth is the standard MieleH256 used by post-commissioning calls, signed
    with the freshly-installed GroupKey. We don't fall back gracefully — a
    failure here is informational, not fatal.
    """
    print(f"\n--- attempting subscription registration ---")
    print(f"  resource: /Devices/{route}/State/")
    print(f"  callback: {callback_url}")
    try:
        sys.path.insert(0, str(ROOT))
        from custom_components.miele_lan.api import MieleLanClient

        async with MieleLanClient.from_hex(device, group_id, group_key, route, timeout=8.0) as c:
            body = {
                "Resource": f"/Devices/{route}/State/",
                "Callback": callback_url,
            }
            payload = json.dumps(body).encode("utf-8")
            # We try BOTH MieleH256-signed (via the client) and raw with MielePairing:Pairing
            # because the right auth tier for /Subscriptions during commissioning isn't yet pinned down.
            for method, path in [("POST", "/Subscriptions"), ("PUT", "/Subscriptions/2"),
                                  ("POST", "/Subscriptions/"), ("PUT", "/Subscriptions/3")]:
                try:
                    status, raw = await c.raw._request_bytes(
                        method, path, body=payload, allowed_status=(200, 201, 202, 204),
                    )
                    print(f"  ✓ {method} {path} → HTTP {status}")
                    if raw:
                        try: print(f"    body: {raw.decode('utf-8', errors='replace')[:200]}")
                        except Exception: pass
                    # Then GET /Subscriptions to confirm it's registered.
                    _, listing = await c.raw._request_bytes("GET", "/Subscriptions", allowed_status=(200,))
                    print(f"  /Subscriptions now: {listing.decode('utf-8', errors='replace')[:160]}")
                    return
                except Exception as e:
                    msg = str(e)
                    code = (msg.split("HTTP error ")[-1][:3] if "HTTP error" in msg else "ERR")
                    print(f"  ✗ {method} {path} → {code}")
            # Second attempt: raw aiohttp with MielePairing:Pairing header (the magic that
            # unlocks /Security/Commissioning/). Worth trying since /Subscriptions/
            # may sit in the same auth bucket.
            print("\n  retrying with Authorization: MielePairing:Pairing header...")
            ssl_ctx = ssl.create_default_context(); ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                for method, path in [("POST", "/Subscriptions"), ("POST", "/Subscriptions/"),
                                      ("PUT", "/Subscriptions/2")]:
                    for scheme in ("http", "https"):
                        url = f"{scheme}://{device}{path}"
                        kw = {"ssl": ssl_ctx} if scheme == "https" else {}
                        try:
                            async with s.request(method, url, data=payload,
                                                  headers={"Authorization": "MielePairing:Pairing",
                                                          "Content-Type": "application/json",
                                                          "Accept": "application/json"},
                                                  **kw) as r:
                                raw = await r.read()
                                print(f"  {method} {scheme}://{device}{path} → {r.status}  {raw[:120]!r}")
                                if 200 <= r.status < 300:
                                    print(f"  🎯 subscription registered with MielePairing auth")
                                    return
                        except Exception as e:
                            print(f"  {method} {scheme}://{device}{path} → {type(e).__name__}")
    except Exception as e:
        print(f"  subscription step error: {type(e).__name__}: {e}")
    print("  (subscription not registered — falling back to polling)")


async def _enable_group_mode(
    device: str, group_id: str, group_key: str, route: str, payload: bytes,
) -> bool:
    """Send the signed PUT /Security/Commissioning/ that the Miele app fires
    right after the unauthenticated commissioning step. This is what flips the
    panel out of 'Warten auf App' / Status=12 (Service). Body is identical to
    the first PUT — same GroupID+GroupKey — but auth is MieleH256 with those
    keys. Returns True on 2xx, False otherwise.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from custom_components.miele_lan.api import MieleLanClient  # noqa: E402
        async with MieleLanClient.from_hex(
            device, group_id, group_key, route, timeout=8.0,
        ) as c:
            status, raw = await c.raw._request_bytes(
                "PUT", "/Security/Commissioning/", body=payload,
                allowed_status=(200, 201, 202, 204),
            )
            print(f"  ✓ signed PUT /Security/Commissioning/ → HTTP {status}")
            if raw:
                print(f"    body: {raw.decode('utf-8', errors='replace')[:200]}")
            return True
    except Exception as e:
        print(f"  ✗ signed PUT /Security/Commissioning/ failed: {type(e).__name__}: {e}")
        return False


async def _detect_route(device: str, group_id: str, group_key: str) -> str | None:
    """One-shot signed GET /Devices to learn the device's route."""
    try:
        sys.path.insert(0, str(ROOT))
        from custom_components.miele_lan.api import MieleLanClient
        # We don't know the route yet — pass a placeholder; only GET /Devices is called.
        async with MieleLanClient.from_hex(device, group_id, group_key, "_unknown", timeout=8.0) as c:
            devices = await c.get_devices()
            keys = list(devices)
            return keys[0] if len(keys) == 1 else None
    except Exception as e:
        print(f"  (route detection skipped: {e})")
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="miele_lan_provision", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    pw = sub.add_parser("wifi", help="Push home-WiFi credentials to a factory-state appliance on its SoftAP.")
    pw.add_argument("--device", default=DEFAULT_DEVICE_IP_SOFTAP, help="Appliance IP on the SoftAP (default %(default)s)")
    pw.add_argument("--ssid", required=True)
    pw.add_argument("--psk", required=True)
    pw.add_argument("--sec", default="WPA2", choices=("WPA", "WPA2", "WPA3", "None"))
    pw.set_defaults(func=cmd_wifi)

    pc = sub.add_parser("commission", help="Push GroupID/GroupKey to a provisioned-but-unkeyed device.")
    pc.add_argument("--device", required=True, help="Appliance IP on the home WiFi (post-WiFi step)")
    pc.add_argument("--out", default=".claude/research/keys.yaml", help="Where to save the new keys")
    pc.add_argument("--endpoint", default="oven", help="Endpoint name to use in keys.yaml")
    pc.add_argument("--no-verify", action="store_true", help="Skip the post-commission signed verify")
    pc.add_argument("--group-id", help="Use this GroupID (16 hex chars) instead of generating one")
    pc.add_argument("--group-key", help="Use this GroupKey (128 hex chars) instead of generating one")
    pc.add_argument("--enable-only", action="store_true",
                    help="Skip the unauthenticated PUT and run only the signed EnableApplianceGroupMode step (use when device is already commissioned but stuck on 'Warten auf App')")
    pc.add_argument(
        "--register-subscription", metavar="URL",
        help="After commissioning succeeds, POST /Subscriptions with this Callback URL "
             "while the device still grants elevated rights. Example: "
             "http://homeassistant.local:8123/api/miele_lan/event. Untested hypothesis — "
             "may return 403 if the window has already closed.",
    )
    pc.set_defaults(func=cmd_commission)

    pv = sub.add_parser("verify", help="Verify stored keys against a live device.")
    pv.add_argument("--device", help="(unused — kept for symmetry)")
    pv.add_argument("--keys", default=".claude/research/keys.yaml")
    pv.add_argument("--endpoint", default="oven")
    pv.set_defaults(func=cmd_verify)
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
