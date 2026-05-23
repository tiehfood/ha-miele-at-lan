"""Miele cloud OAuth + household GroupKey retrieval.

Implements the MAP/Gigya OAuth flow (per-country consumer client_id, PKCE,
redirect_uri=miele://oauth2-code/) and the household-key fetch at
GET https://rest-{region}.domestic.miele-iot.com/V2/GroupKeyId/.

The integration uses this to obtain the household secret without local
provisioning, preserving cloud pairing so the Miele app keeps working.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import urllib.parse
from dataclasses import dataclass
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Per-country MAP consumer client_ids (extracted from official Android APK).
# Same Gigya tenant `4_j_ZiR1Ejz2QKP4NFcYnOZw` is shared; only the MAP wrapper
# differs per country.
CONSUMER_CLIENT_IDS: dict[str, str] = {
    "at": "wNv9HJ3ZcFKH4bxvz0LExQuw",
    "ch": "V52nWiniHyVotglJKplSXnX8",
    "cz": "npoAzuJP6okjvJ0NqUq9i5Rv",
    "de": "UJgKOxacIul2BcPJAzrQE6p0",
    "dk": "xWgykqRQSa9THqOXWfzZbxsH",
    "es": "D0Q4NPBR9dwP2EjX4E0_CtHE",
    "fr": "SOiiE3R4tSD0VxYYBvB8Pi_J",
    "gb": "WigtLzKGJE1Wg6yeZUECV8-P",
    "hr": "HD4OUUQYAw_5DtVFSe4-rYzR",
    "hu": "2mm2yscHPGJ4tJCVjd6mp-to",
    "it": "ARQyaYB0ZxLxJ1SJcjJgctuV",
    "nl": "7ItTbQXQ1wthDOue9jvBQ7Iz",
    "pl": "jWbgLScpvIuqjUoYvf1jS-Is",
    "pt": "5ZVD-CuJvpG4YpCO9pQhtrGQ",
    "se": "3Mm7m1gD1eU_sUh8yxmShL6S",
    "si": "UTyhG21RchpI8FPbNeb1vFg1",
    "sk": "pGeafLwcC1_BCLr8DRTCVxSt",
    "us": "HpsWh2gzgKqRBduPpkZ4Yui9",
}

REDIRECT_URI = "miele://oauth2-code/"
OAUTH_SCOPE = "openid mcs bpdata zuora"  # `mcs` is mandatory for /V2/GroupKeyId/

# Sales-company region → REST host that serves /V2/GroupKeyId/.
# From bundled Production.json in Miele.Libraries.Foundation.Platform.Locales.V3.dll.
REST_HOST_BY_REGION: dict[str, str] = {
    "EU": "rest-eu.domestic.miele-iot.com",
    "AS": "rest-as.domestic.miele-iot.com",
    "EU2": "rest-eu2.domestic.miele-iot.com",
}


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@dataclass
class PKCEChallenge:
    """One-shot OAuth challenge — kept across the user's browser round-trip."""

    verifier: str
    challenge: str
    state: str
    nonce: str
    cc: str
    client_id: str


@dataclass
class GroupKey:
    """Household key + the cloud-listed devices that share it."""

    group_id: str
    group_key: str
    devices: list[dict[str, Any]]


def build_authorize_url(cc: str) -> tuple[str, PKCEChallenge]:
    """Build the URL the user must open in a browser.

    Returns (url, challenge). Persist `challenge` somewhere until the user
    pastes the redirect — exchange_code() needs the verifier + state to
    complete the flow safely.
    """
    cc = cc.lower()
    if cc not in CONSUMER_CLIENT_IDS:
        raise ValueError(
            f"Unknown country {cc!r}; supported: {sorted(CONSUMER_CLIENT_IDS)}"
        )
    client_id = CONSUMER_CLIENT_IDS[cc]
    verifier = _b64url(secrets.token_bytes(64))
    challenge_hash = _b64url(hashlib.sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))
    nonce = _b64url(secrets.token_bytes(16))

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": OAUTH_SCOPE,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge_hash,
        "code_challenge_method": "S256",
    }
    url = (
        f"https://prod.map.miele-iot.com/{cc}/authorize?"
        + urllib.parse.urlencode(params)
    )
    return url, PKCEChallenge(
        verifier=verifier,
        challenge=challenge_hash,
        state=state,
        nonce=nonce,
        cc=cc,
        client_id=client_id,
    )


def parse_redirect_url(redirect_url: str, expected_state: str | None = None) -> str:
    """Extract `code` from a `miele://oauth2-code/?code=...&state=...` URL.

    Verifies `state` matches the value we issued (CSRF protection) when
    `expected_state` is provided. Raises ValueError on any anomaly.
    """
    qs = urllib.parse.urlparse(redirect_url).query
    parsed = urllib.parse.parse_qs(qs)
    if "error" in parsed:
        raise ValueError(
            f"OAuth error: {parsed['error'][0]} {parsed.get('error_description', [''])[0]}"
        )
    code = parsed.get("code", [None])[0]
    if not code:
        raise ValueError(f"no `code` in redirect URL: {redirect_url[:120]!r}")
    state = parsed.get("state", [None])[0]
    if expected_state is not None and state != expected_state:
        raise ValueError(f"state mismatch: got {state!r}, expected {expected_state!r}")
    return code


async def exchange_code(
    session: aiohttp.ClientSession, challenge: PKCEChallenge, code: str
) -> dict[str, Any]:
    """Exchange a one-shot `code` for {access_token, refresh_token, …}.

    Returns the raw token response. Caller is responsible for storing
    `refresh_token` securely.
    """
    url = f"https://prod.map.miele-iot.com/{challenge.cc}/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": challenge.client_id,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": challenge.verifier,
    }
    async with session.post(
        url, data=data, timeout=aiohttp.ClientTimeout(total=15)
    ) as r:
        body = await r.text()
        try:
            tokens = await r.json(content_type=None)
        except Exception as err:
            raise RuntimeError(f"non-JSON token response: {body[:300]}") from err
    if "error" in tokens:
        raise RuntimeError(
            f"token endpoint returned error: {tokens['error']} "
            f"{tokens.get('error_description', '')}"
        )
    return tokens


async def refresh_access_token(
    session: aiohttp.ClientSession, cc: str, refresh_token: str
) -> dict[str, Any]:
    """Renew the access token. Returns {access_token, refresh_token?, expires_in, …}.

    May rotate the refresh_token — caller should store whichever value the
    response carries (or fall back to the original if not rotated).
    """
    client_id = CONSUMER_CLIENT_IDS[cc.lower()]
    url = f"https://prod.map.miele-iot.com/{cc.lower()}/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    async with session.post(
        url, data=data, timeout=aiohttp.ClientTimeout(total=15)
    ) as r:
        tokens = await r.json(content_type=None)
    if "error" in tokens:
        raise RuntimeError(
            f"refresh failed: {tokens['error']} "
            f"{tokens.get('error_description', '')}"
        )
    return tokens


async def fetch_groupkey(
    session: aiohttp.ClientSession,
    access_token: str,
    region: str = "EU",
) -> GroupKey:
    """GET /V2/GroupKeyId/ → household key + device list.

    `region` derives from the sales company. EU covers DE/AT/CH/etc.
    """
    host = REST_HOST_BY_REGION.get(region.upper())
    if not host:
        raise ValueError(f"unknown region {region!r}")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "User-Agent": "Miele@LAN/0.3 (Home Assistant)",
    }
    async with session.get(
        f"https://{host}/V2/GroupKeyId/",
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        if r.status == 403:
            body = (await r.text())[:200]
            raise RuntimeError(
                f"GroupKeyId returned 403 — token likely missing `mcs` scope. body={body}"
            )
        r.raise_for_status()
        groups = await r.json(content_type=None)
    if not groups:
        raise RuntimeError("no household returned — account has no paired devices?")
    g = groups[0]  # exactly one household per account in normal use
    return GroupKey(
        group_id=g["groupId"],
        group_key=g["groupKey"],
        devices=list(g.get("devices") or []),
    )


async def fetch_pairing_tan(
    session: aiohttp.ClientSession,
    access_token: str,
    region: str = "EU",
) -> str:
    """GET /V2/TAN/ → cloud-issued one-shot TAN for the next commissioning.

    Used by `tools/miele_lan_provision.py --use-cloud-tan` to satisfy the
    EK039W (and similar) firmware's TAN check before PUT /Security/Commissioning/
    will accept our keys. The cloud out-of-band notifies the appliance over its
    `wss://websocket-eu.mcs2.miele.com` channel that this TAN is expected, then
    the caller POSTs it to the appliance's `/Security/Cloud/TAN/`.

    Returns the bare TAN string from the `Tan` field. Raises on non-200.
    """
    host = REST_HOST_BY_REGION.get(region.upper())
    if not host:
        raise ValueError(f"unknown region {region!r}")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "User-Agent": "Miele@LAN/0.3 (Home Assistant)",
    }
    async with session.get(
        f"https://{host}/V2/TAN/",
        headers=headers,
        timeout=aiohttp.ClientTimeout(total=15),
    ) as r:
        if r.status != 200:
            raise RuntimeError(
                f"/V2/TAN/ returned {r.status}: {(await r.text())[:200]!r}"
            )
        data = await r.json(content_type=None)
    # The cloud responds with lowercase `tan` (live), even though the decompiled
    # C# DTO is `TanDto.Tan`. Accept either spelling to stay robust.
    tan = (data or {}).get("tan") or (data or {}).get("Tan")
    if not tan:
        raise RuntimeError(f"/V2/TAN/ response missing tan field: {data!r}")
    return str(tan)


__all__ = [
    "CONSUMER_CLIENT_IDS",
    "REDIRECT_URI",
    "OAUTH_SCOPE",
    "REST_HOST_BY_REGION",
    "PKCEChallenge",
    "GroupKey",
    "build_authorize_url",
    "parse_redirect_url",
    "exchange_code",
    "refresh_access_token",
    "fetch_groupkey",
    "fetch_pairing_tan",
]
