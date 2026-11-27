"""Tests for sales companies that log in through their own OIDC endpoint.

Most countries use the MAP gateway (`prod.map.miele-iot.com/<cc>/...`), but the
live `/V3/Locales?org=<ISO>` document points a few (PL, IT) at
`api.miele.com/v1/ciam/oidc/op/<apiKey>/...`. The MAP URL has no client
registered for those, so cloud setup failed before the login form.

`cloud.py` is loaded straight off disk (see test_cloud_regions.py), and all HTTP
is faked: nothing here touches the network.
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_cloud_oidc", ROOT / "custom_components" / "miele_lan" / "cloud.py"
)
cloud = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_cloud_oidc"] = cloud
_spec.loader.exec_module(cloud)

# Placeholders only: nothing below is a real identifier or credential.
OP_SEGMENT = "placeholder-op-segment"
PL_AUTHORIZE = f"https://api.miele.com/v1/ciam/oidc/op/{OP_SEGMENT}/authorize"
PL_TOKEN = f"https://api.miele.com/v1/ciam/oidc/op/{OP_SEGMENT}/token"
ECHOED_ID = "placeholder-echoed-id"


def _locales(authorize: str, token: str) -> list[dict]:
    return [
        {
            "ciam": {
                "active": True,
                "endpoints": [
                    {"type": "authorize", "uri": authorize},
                    {"type": "token", "uri": token},
                    {"type": "logout", "uri": "https://example.invalid/logout"},
                ],
            }
        }
    ]


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def json(self, content_type=None):
        return self._body

    async def text(self):
        return str(self._body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Routes GET/POST by URL; records the calls."""

    def __init__(self, routes, post_routes=None):
        self.routes = routes  # url -> _Resp | Exception
        self.post_routes = post_routes or {}
        self.gets: list[str] = []
        self.posts: list[tuple[str, dict]] = []

    def get(self, url, params=None, **kwargs):
        self.gets.append(url)
        hit = self.routes.get(url)
        if hit is None and url == cloud.LOCALES_URL:
            hit = self.routes.get("locales")
        if isinstance(hit, Exception):
            raise hit
        return hit if hit is not None else _Resp(404)

    def post(self, url, data=None, **kwargs):
        self.posts.append((url, data))
        return self.post_routes.get(url, _Resp(200, {"access_token": "a"}))


def setup_function(_function):
    """resolve_oidc() now caches per country for the process; each test below
    reuses `pl`/`de` with different fake responses, so start from an empty cache."""
    cloud._OIDC_CACHE.clear()
    cloud._OIDC_CACHE_LOCKS.clear()


def _run(coro):
    return asyncio.run(coro)


def _pl_session(location=f"https://idp.example/err?client_id={ECHOED_ID}&mode=error"):
    return FakeSession(
        {
            "locales": _Resp(200, _locales(PL_AUTHORIZE, PL_TOKEN)),
            PL_AUTHORIZE: _Resp(302, None, {"Location": location}),
        }
    )


def test_resolve_oidc_for_own_endpoint():
    o = _run(cloud.resolve_oidc(_pl_session(), "pl"))
    assert o == cloud.OidcOverride(PL_AUTHORIZE, PL_TOKEN, ECHOED_ID)


def test_resolve_oidc_none_for_map_gateway():
    s = FakeSession(
        {
            "locales": _Resp(
                200,
                _locales(
                    "https://prod.map.miele-iot.com/de/authorize",
                    "https://prod.map.miele-iot.com/de/token",
                ),
            )
        }
    )
    assert _run(cloud.resolve_oidc(s, "de")) is None


def test_resolve_oidc_none_when_locales_fail():
    for routes in (
        {"locales": _Resp(500)},
        {"locales": ConnectionError("down")},
        {"locales": _Resp(200, [])},
        {"locales": _Resp(200, [{"ciam": {"active": False, "endpoints": []}}])},
    ):
        cloud._OIDC_CACHE.clear()  # each case must actually run, not hit the last one's cache
        assert _run(cloud.resolve_oidc(FakeSession(routes), "pl")) is None


def test_locales_failure_is_not_cached():
    """A 500/timeout must not stick as "MAP gateway applies" for the process."""

    class FlakyThenFixedSession(FakeSession):
        """Locales 500s once, then serves the real document; the authorize
        probe (second GET, once Locales recovers) echoes a client_id."""

        def __init__(self):
            super().__init__({})
            self._locales_calls = 0

        def get(self, url, params=None, **kwargs):
            self.gets.append(url)
            if url == cloud.LOCALES_URL:
                self._locales_calls += 1
                if self._locales_calls == 1:
                    return _Resp(500)
                return _Resp(200, _locales(PL_AUTHORIZE, PL_TOKEN))
            if url == PL_AUTHORIZE:
                return _Resp(302, None, {"Location": f"https://idp.example/err?client_id={ECHOED_ID}"})
            return _Resp(404)

    s = FlakyThenFixedSession()
    assert _run(cloud.resolve_oidc(s, "pl")) is None  # Locales 500: not cached
    assert cloud._OIDC_CACHE == {}

    second = _run(cloud.resolve_oidc(s, "pl"))  # retried: Locales answers this time
    assert second == cloud.OidcOverride(PL_AUTHORIZE, PL_TOKEN, ECHOED_ID)
    assert cloud._OIDC_CACHE == {"pl": second}  # now cached
    assert s._locales_calls == 2


def test_client_id_falls_back_to_table_when_probe_fails():
    # No Location header on the authorize probe.
    o = _run(cloud.resolve_oidc(_pl_session(location=""), "pl"))
    assert o is not None
    assert o.client_id == cloud.CONSUMER_CLIENT_IDS["pl"]


def test_async_build_uses_own_endpoint():
    url, ch = _run(cloud.async_build_authorize_url(_pl_session(), "pl"))
    assert url.startswith(PL_AUTHORIZE + "?")
    assert f"client_id={ECHOED_ID}" in url
    assert "redirect_uri=miele%3A%2F%2Foauth2-code%2F" in url
    assert ch.token_url == PL_TOKEN
    assert ch.client_id == ECHOED_ID


def test_authorize_url_keeps_a_query_the_endpoint_already_has():
    # Importer countries (Israel, #96): /V3/Locales names `...authorize?login_hint=IL`.
    il_authorize = PL_AUTHORIZE + "?login_hint=IL"
    s = FakeSession(
        {
            "locales": _Resp(200, _locales(il_authorize, PL_TOKEN)),
            il_authorize: _Resp(302, None, {"Location": f"https://idp.example/err?client_id={ECHOED_ID}"}),
        }
    )
    url, ch = _run(cloud.async_build_authorize_url(s, "il"))
    assert url.startswith(il_authorize + "&client_id=" + ECHOED_ID + "&")
    assert url.count("?") == 1
    assert ch.token_url == PL_TOKEN
    assert ch.cc == "il"


def test_israel_can_be_selected():
    assert "il" in cloud.CONSUMER_CLIENT_IDS
    assert cloud.region_candidates("il")[0] == "EU"


def test_async_build_keeps_map_when_lookup_fails():
    s = FakeSession({"locales": ConnectionError("down")})
    url, ch = _run(cloud.async_build_authorize_url(s, "de"))
    assert url.startswith("https://prod.map.miele-iot.com/de/authorize?")
    assert ch.token_url is None
    assert ch.client_id == cloud.CONSUMER_CLIENT_IDS["de"]


def test_exchange_code_posts_to_challenge_token_url():
    _, ch = _run(cloud.async_build_authorize_url(_pl_session(), "pl"))
    s = FakeSession({}, {PL_TOKEN: _Resp(200, {"access_token": "a"})})
    assert _run(cloud.exchange_code(s, ch, "code123")) == {"access_token": "a"}
    url, data = s.posts[0]
    assert url == PL_TOKEN
    assert data["client_id"] == ECHOED_ID and data["code"] == "code123"


def test_exchange_code_default_url_unchanged():
    url, ch = cloud.build_authorize_url("de")
    s = FakeSession({}, {})
    _run(cloud.exchange_code(s, ch, "c"))
    assert s.posts[0][0] == "https://prod.map.miele-iot.com/de/token"


def test_refresh_uses_own_token_endpoint():
    s = _pl_session()
    s.post_routes[PL_TOKEN] = _Resp(200, {"access_token": "a2"})
    assert _run(cloud.refresh_access_token(s, "pl", "r")) == {"access_token": "a2"}
    url, data = s.posts[0]
    assert url == PL_TOKEN and data["client_id"] == ECHOED_ID


def test_refresh_default_url_unchanged():
    s = FakeSession({"locales": _Resp(500)})
    _run(cloud.refresh_access_token(s, "de", "r"))
    assert s.posts[0][0] == "https://prod.map.miele-iot.com/de/token"


def test_resolve_oidc_is_cached_per_country():
    s = _pl_session()
    first = _run(cloud.resolve_oidc(s, "pl"))
    assert len(s.gets) == 2  # locales + the authorize probe
    second = _run(cloud.resolve_oidc(s, "PL"))  # case-insensitive, same entry
    assert second == first
    assert len(s.gets) == 2  # no new request on the cache hit


def test_resolve_oidc_caches_none_for_map_gateway_too():
    s = FakeSession(
        {
            "locales": _Resp(
                200,
                _locales(
                    "https://prod.map.miele-iot.com/de/authorize",
                    "https://prod.map.miele-iot.com/de/token",
                ),
            )
        }
    )
    assert _run(cloud.resolve_oidc(s, "de")) is None
    assert _run(cloud.resolve_oidc(s, "de")) is None
    assert len(s.gets) == 1
