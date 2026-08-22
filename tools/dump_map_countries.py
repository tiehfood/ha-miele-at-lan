"""Miele@LAN — regenerate the MAP consumer client_id table.

The per-country consumer `client_id` values in `custom_components/miele_lan/cloud.py`
were originally lifted out of the official Android APK. They do not have to be:
Miele's MAP gateway hands them out itself, unauthenticated.

  GET https://prod.map.miele-iot.com/{cc}/authorize?client_id=<anything>&...

returns `302` whose `Location` points at the Gigya OIDC endpoint for that sales
company, and that URL carries the *real* `client_id` — MAP rewrites whatever we
sent. An unrecognised country returns `200` with no `Location`, so the same probe
doubles as a validity check.

The redirect also names the Gigya data centre (`fidm.<dc>.gigya.com`), which is
what tells us whether a household's keys live on the EU or the AS REST backend.

Run:
  python tools/dump_map_countries.py            # print CONSUMER_CLIENT_IDS + GIGYA_DC
  python tools/dump_map_countries.py --check    # diff against cloud.py, exit 1 on drift
  python tools/dump_map_countries.py --cc au nz # probe specific countries only

Verified 2026-08-22: all 18 countries that were extracted from the APK reproduce
byte-for-byte through this probe, so the two sources agree where they overlap.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import urllib.parse
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent

MAP_HOST = "https://prod.map.miele-iot.com"
REDIRECT_URI = "miele://oauth2-code/"

# ISO 3166-1 alpha-2 codes worth probing. Miele runs sales companies in a subset;
# unrecognised codes are simply skipped, so over-probing costs only a round trip.
CANDIDATE_COUNTRIES = (
    "ad ae ar at au be bg br ca ch cl cn co cy cz de dk ee eg es fi fr gb gr hk "
    "hr hu id ie il in is it jp kr kw kz lt lu lv ma mx my nl no nz pe ph pl pt "
    "qa ro rs ru sa se sg si sk th tr tw ua us vn za"
).split()

_GIGYA_RE = re.compile(r"fidm\.(?P<dc>[a-z0-9]+)\.gigya\.com")


async def probe(session: aiohttp.ClientSession, cc: str) -> tuple[str, str, str] | None:
    """Return (cc, gigya_dc, client_id), or None when MAP doesn't know `cc`."""
    params = {
        "client_id": "probe",  # deliberately bogus — MAP substitutes the real one
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "openid",
        "state": "probe",
    }
    url = f"{MAP_HOST}/{cc}/authorize?" + urllib.parse.urlencode(params)
    async with session.get(
        url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=15)
    ) as r:
        location = r.headers.get("Location")
    if not location:
        return None
    dc_match = _GIGYA_RE.search(location)
    client_id = urllib.parse.parse_qs(
        urllib.parse.urlparse(location).query
    ).get("client_id", [None])[0]
    if not dc_match or not client_id:
        return None
    return cc, dc_match.group("dc"), client_id


async def probe_all(countries: list[str]) -> list[tuple[str, str, str]]:
    """Probe every country concurrently; drop the ones MAP doesn't recognise."""
    sem = asyncio.Semaphore(8)  # be gentle with a third party's gateway

    async def one(session: aiohttp.ClientSession, cc: str):
        async with sem:
            try:
                return await probe(session, cc)
            except Exception as err:  # noqa: BLE001
                print(f"  ! {cc}: {err}", file=sys.stderr)
                return None

    # Force the stdlib resolver: aiohttp prefers aiodns when it is importable, and
    # a Home Assistant venv can carry an aiodns/pycares pair that raises on every
    # lookup. Nothing here needs an async resolver, so sidestep it entirely.
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as session:
        results = await asyncio.gather(*(one(session, cc) for cc in countries))
    return sorted(r for r in results if r)


def render(rows: list[tuple[str, str, str]]) -> str:
    """Emit both tables in the exact shape `cloud.py` carries them."""
    ids = "\n".join(f'    "{cc}": "{cid}",' for cc, _, cid in rows)
    dcs = "\n".join(f'    "{cc}": "{dc}",' for cc, dc, _ in rows)
    return (
        f"CONSUMER_CLIENT_IDS: dict[str, str] = {{\n{ids}\n}}\n\n"
        f"GIGYA_DC_BY_COUNTRY: dict[str, str] = {{\n{dcs}\n}}\n"
    )


def check(rows: list[tuple[str, str, str]]) -> int:
    """Compare the live probe against the table currently in cloud.py."""
    sys.path.insert(0, str(ROOT))
    from custom_components.miele_lan.cloud import (  # noqa: E402
        CONSUMER_CLIENT_IDS,
        GIGYA_DC_BY_COUNTRY,
    )

    live_ids = {cc: cid for cc, _, cid in rows}
    live_dcs = {cc: dc for cc, dc, _ in rows}
    drift = 0
    for cc, cid in sorted(live_ids.items()):
        if cc not in CONSUMER_CLIENT_IDS:
            print(f"MISSING  {cc}: MAP serves {cid}, cloud.py has no entry")
            drift += 1
        elif CONSUMER_CLIENT_IDS[cc] != cid:
            print(f"CHANGED  {cc}: cloud.py={CONSUMER_CLIENT_IDS[cc]} MAP={cid}")
            drift += 1
    for cc in sorted(set(CONSUMER_CLIENT_IDS) - set(live_ids)):
        print(f"STALE    {cc}: in cloud.py but MAP no longer recognises it")
        drift += 1
    for cc, dc in sorted(live_dcs.items()):
        if GIGYA_DC_BY_COUNTRY.get(cc) not in (None, dc):
            print(f"DC MOVED {cc}: cloud.py={GIGYA_DC_BY_COUNTRY[cc]} MAP={dc}")
            drift += 1
    print(f"\n{len(live_ids)} countries served by MAP; {drift} discrepancies.")
    return 1 if drift else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--cc", nargs="+", metavar="CC",
        help="probe only these country codes (default: the built-in candidate list)",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="diff the probe against cloud.py and exit non-zero on drift",
    )
    args = parser.parse_args()

    countries = [c.lower() for c in (args.cc or CANDIDATE_COUNTRIES)]
    rows = asyncio.run(probe_all(countries))
    if not rows:
        print("no countries answered — network blocked, or MAP changed shape?",
              file=sys.stderr)
        return 2
    if args.check:
        return check(rows)
    print(render(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
