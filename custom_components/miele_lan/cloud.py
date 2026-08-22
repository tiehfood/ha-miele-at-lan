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

# Per-country MAP consumer client_ids. Regenerate with
# `python tools/dump_map_countries.py`: Miele's MAP gateway serves these itself
# over an unauthenticated redirect, so they no longer have to be lifted out of
# the Android APK. The 18 countries that were originally APK-extracted reproduce
# byte-for-byte through that probe, which is what pins the two sources together.
# (The old note here claimed every country shares one Gigya tenant — it does not:
# each sales company has its own tenant, in one of three Gigya data centres.)
CONSUMER_CLIENT_IDS: dict[str, str] = {
    "ae": "iaXIbUAGv6QTUd8FRNPG5fHN",
    "at": "wNv9HJ3ZcFKH4bxvz0LExQuw",
    "au": "rLXHMAq2VdnICd0eHuefA50w",
    "be": "JFDW4KGg_ppC3kmSOITHNKBp",
    "bg": "v6R8ClqnHRWVYJBlhxah0fiR",
    "br": "zu0ZoLtw2HUj5GH_I_nHfQxM",
    "ca": "-fZUlMeq5FaRsG5UNgtMpr-e",
    "ch": "V52nWiniHyVotglJKplSXnX8",
    "cl": "_BRdHKYtaYRpqk7j67y1mc4W",
    "cn": "_4vxrtrxGJeUNaBd4zTkeSZ2",
    "cy": "9se3MtP42BbyWUIQBaE9c8NE",
    "cz": "npoAzuJP6okjvJ0NqUq9i5Rv",
    "de": "UJgKOxacIul2BcPJAzrQE6p0",
    "dk": "xWgykqRQSa9THqOXWfzZbxsH",
    "ee": "m_4y2qk8ntJbOqu6kx7PYXOB",
    "es": "D0Q4NPBR9dwP2EjX4E0_CtHE",
    "fi": "8JbG7QQsdvEMsYisC_nUfltr",
    "fr": "SOiiE3R4tSD0VxYYBvB8Pi_J",
    "gb": "WigtLzKGJE1Wg6yeZUECV8-P",
    "gr": "meyCOw7Co2ICAx5ZCMb21oiE",
    "hk": "tfbB9jidNn1ahn3_oiTrv8um",
    "hr": "HD4OUUQYAw_5DtVFSe4-rYzR",
    "hu": "2mm2yscHPGJ4tJCVjd6mp-to",
    "ie": "_UU4TA2Iei5eEf0XkYp3ZRKF",
    "in": "5KwIy81GoVqNkxkB20OtTNaf",
    "it": "ARQyaYB0ZxLxJ1SJcjJgctuV",
    "jp": "wk4iaS04nYkN4seEsZbKQ2xB",
    "kr": "_e5CgRBpM9h9ecVoUhpZl9-h",
    "lt": "KzeuROL469pqvGFjSYp2ivQ2",
    "lu": "34UAt-gA9ADx2DWgBbtoQh6X",
    "lv": "MJhu3mAsyk7Tahso0i_7m1ud",
    "mx": "G_FW4x4rXuwJ6v2VFx2REn34",
    "my": "esJDemY095qO2Os6VPhAtky0",
    "nl": "7ItTbQXQ1wthDOue9jvBQ7Iz",
    "no": "411mmVRPOnTNmViGl5aDc5h8",
    "nz": "RtYo_k74PpIFsLBsrcRk9238",
    "pl": "jWbgLScpvIuqjUoYvf1jS-Is",
    "pt": "5ZVD-CuJvpG4YpCO9pQhtrGQ",
    "ro": "AQJyFJC8kgsHvKnC2druvJn1",
    "rs": "qbkOxTdY-dx-0AswysR75wU-",
    "se": "3Mm7m1gD1eU_sUh8yxmShL6S",
    "sg": "x798Jlx93o8YAL3Lg0dDwdzU",
    "si": "UTyhG21RchpI8FPbNeb1vFg1",
    "sk": "pGeafLwcC1_BCLr8DRTCVxSt",
    "th": "kY1LYjhV5Wy8L3Jxe9rh_vYr",
    "tr": "ZSHeNOPc9DcFfqNY42LQAIdl",
    "ua": "bDsz9awSMOt-631KZK2J7dUg",
    "us": "HpsWh2gzgKqRBduPpkZ4Yui9",
    "za": "A0sngQGYP8ZvfZ100eJGZD4M",
}

# Country -> Gigya data centre backing that sales company, from the same probe.
# Used only to order the REST backends in `region_candidates()`.
GIGYA_DC_BY_COUNTRY: dict[str, str] = {
    "ae": "eu1",
    "at": "eu1",
    "au": "au1",
    "be": "eu1",
    "bg": "eu1",
    "br": "us1",
    "ca": "us1",
    "ch": "eu1",
    "cl": "us1",
    "cn": "eu1",
    "cy": "eu1",
    "cz": "eu1",
    "de": "eu1",
    "dk": "eu1",
    "ee": "eu1",
    "es": "eu1",
    "fi": "eu1",
    "fr": "eu1",
    "gb": "eu1",
    "gr": "eu1",
    "hk": "us1",
    "hr": "eu1",
    "hu": "eu1",
    "ie": "eu1",
    "in": "eu1",
    "it": "eu1",
    "jp": "us1",
    "kr": "us1",
    "lt": "eu1",
    "lu": "eu1",
    "lv": "eu1",
    "mx": "us1",
    "my": "au1",
    "nl": "eu1",
    "no": "eu1",
    "nz": "au1",
    "pl": "eu1",
    "pt": "eu1",
    "ro": "eu1",
    "rs": "eu1",
    "se": "eu1",
    "sg": "au1",
    "si": "eu1",
    "sk": "eu1",
    "th": "au1",
    "tr": "eu1",
    "ua": "eu1",
    "us": "us1",
    "za": "eu1",
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

# Only two backends sit behind those names: `rest-eu` and `rest-eu2` resolve to one
# address, `rest-as` and `rest-as2` to another (checked 2026-08-22). That is why a
# wrong first guess is cheap to recover from — see `region_candidates()`.


def region_candidates(cc: str) -> list[str]:
    """REST regions to try, best guess first, for a sales company.

    The Gigya data centre named in the MAP authorize redirect is the only signal
    available, and it demonstrably does NOT map to a REST backend: an `au`
    account sits in Gigya's au1 data centre while its household lives on
    rest-eu (captured from the Miele iOS app, 2026-08-22). Treat this purely as
    which backend to ask first; `fetch_groupkey` must try the others regardless. Since `/V2/GroupKeyId/` is a plain
    GET against one of two hosts, the caller can just try both and keep whichever
    serves the household. That beats maintaining a country -> region table nobody
    can verify for every market, which is what previously limited setup to the EU.
    """
    dc = GIGYA_DC_BY_COUNTRY.get(cc.lower(), "eu1")
    return ["AS", "EU"] if dc == "au1" else ["EU", "AS"]


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
    region: str = ""  # REST backend that answered — persist to skip the search later


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


def _pick_household(
    groups: list[dict[str, Any]], prefer_group_ids: set[str] | None
) -> dict[str, Any]:
    """Choose which household to set up when the cloud returns several.

    The old code took `groups[0]` and called one household per account "normal".
    It is not: an account that has been re-paired, or had appliances replaced,
    keeps the retired households, and the stale one can sort first — observed on
    an `au` account whose first entry had no devices while the live household
    held three (2026-08-22).

    Preference order:
      1. a household the LAN is actually advertising — mDNS is the only
         authoritative answer to "which of these is in front of me"
      2. one that lists devices, since an empty list cannot be set up
      3. the first, preserving the previous behaviour
    """
    if prefer_group_ids:
        for g in groups:
            if (g.get("groupId") or "").upper() in prefer_group_ids:
                return g
        _LOGGER.debug(
            "no cloud household matches the LAN groups %s — falling back",
            sorted(prefer_group_ids),
        )
    return next((g for g in groups if g.get("devices")), groups[0])


async def _groupkey_from_host(
    session: aiohttp.ClientSession,
    access_token: str,
    host: str,
    prefer_group_ids: set[str] | None = None,
) -> GroupKey | None:
    """One backend's answer to /V2/GroupKeyId/, or None when it holds no household.

    Raises on 403: that means the token is missing the `mcs` scope, which is a
    token problem rather than a wrong-backend one, so the other host cannot help.
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        # Keep sending this even though the payload carries no localised text.
        # Dropping it made both backends answer HTTP 500 for an `au` account
        # (2026-08-22), so something server-side wants a locale present. The
        # value itself appears not to matter, only that there is one — leave it
        # at the value the EU-only builds shipped rather than inventing a
        # per-country locale we cannot test.
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
        if r.status != 200:
            _LOGGER.debug(
                "%s answered HTTP %s for /V2/GroupKeyId/: %s",
                host, r.status, (await r.text())[:300],
            )
            return None
        groups = await r.json(content_type=None)
    if not groups:
        _LOGGER.debug("%s knows no household for this token", host)
        return None
    _LOGGER.debug(
        "%s served %d household(s): %s",
        host, len(groups),
        [
            (g.get("groupId"), len(g.get("devices") or []))
            for g in groups
        ],
    )
    g = _pick_household(groups, prefer_group_ids)
    return GroupKey(
        group_id=g["groupId"],
        group_key=g["groupKey"],
        devices=list(g.get("devices") or []),
    )


async def fetch_groupkey(
    session: aiohttp.ClientSession,
    access_token: str,
    region: str | None = None,
    cc: str | None = None,
    prefer_group_ids: set[str] | None = None,
) -> GroupKey:
    """GET /V2/GroupKeyId/ → household key + device list.

    `prefer_group_ids` are the households mDNS can see on this LAN; when the
    account owns more than one, that is what decides which is the live one.

    Pass `region` when it is already known — a configured entry records the one
    that worked. Leave it None and the backends from `region_candidates(cc)` are
    tried in order until one returns a household; the winner comes back on
    `GroupKey.region`, so setup works for a sales company whose backend we have
    never seen without anyone having to hand-maintain a country → region table.
    """
    regions = [region.upper()] if region else region_candidates(cc or "de")
    wanted = {g.upper() for g in (prefer_group_ids or set())}
    fallback: GroupKey | None = None

    for candidate in regions:
        host = REST_HOST_BY_REGION.get(candidate)
        if not host:
            raise ValueError(f"unknown region {candidate!r}")
        groupkey = await _groupkey_from_host(
            session, access_token, host, prefer_group_ids
        )
        if not groupkey:
            continue
        groupkey.region = candidate

        # Nothing to match against: first backend that answers wins, as before.
        if not wanted:
            return groupkey

        if groupkey.group_id.upper() in wanted:
            return groupkey

        # A backend answered, but with a household this LAN knows nothing about.
        # Keep looking — the other backend may hold the one we actually want.
        # An `au` account is served by rest-eu despite sitting in Gigya's au1
        # data centre, and stopping at the first answer picked up an unrelated
        # empty household from rest-as while the live one sat on rest-eu.
        _LOGGER.debug(
            "%s offered household %s, which is not on this LAN — trying the rest",
            host, groupkey.group_id,
        )
        if fallback is None:
            fallback = groupkey

    if fallback is not None:
        _LOGGER.warning(
            "no backend offered a household matching this LAN (%s); falling back "
            "to %s from %s", sorted(wanted), fallback.group_id, fallback.region,
        )
        return fallback

    raise RuntimeError(
        f"no household returned by {', '.join(regions)} — the account has no "
        f"paired devices, or its keys live on a backend we did not try"
    )


async def fetch_pairing_tan(
    session: aiohttp.ClientSession,
    access_token: str,
    region: str | None = None,
    cc: str | None = None,
) -> str:
    """GET /V2/TAN/ → cloud-issued one-shot TAN for the next commissioning.

    Used by `tools/miele_lan_provision.py --use-cloud-tan` to satisfy the
    EK039W (and similar) firmware's TAN check before PUT /Security/Commissioning/
    will accept our keys. The cloud out-of-band notifies the appliance over its
    `wss://websocket-eu.mcs2.miele.com` channel that this TAN is expected, then
    the caller POSTs it to the appliance's `/Security/Cloud/TAN/`.

    Returns the bare TAN string from the `Tan` field. Raises on non-200.
    """
    # Unlike GroupKeyId this issues a one-shot TAN, so it must not be probed
    # across backends — take the caller's region, or the best guess for `cc`.
    region = (region or region_candidates(cc or "de")[0]).upper()
    host = REST_HOST_BY_REGION.get(region)
    if not host:
        raise ValueError(f"unknown region {region!r}")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Accept-Language": "de-DE",  # see _groupkey_from_host
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
    "GIGYA_DC_BY_COUNTRY",
    "region_candidates",
    "_pick_household",
    "PKCEChallenge",
    "GroupKey",
    "build_authorize_url",
    "parse_redirect_url",
    "exchange_code",
    "refresh_access_token",
    "fetch_groupkey",
    "fetch_pairing_tan",
]
