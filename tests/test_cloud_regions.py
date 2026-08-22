"""Tests for the per-country MAP tables and REST backend selection.

`cloud.py` is loaded straight off disk rather than as `custom_components.miele_lan.cloud`,
because importing the package pulls in `homeassistant`; this module only needs
aiohttp and the stdlib.

All offline — the live probe lives in `tools/dump_map_countries.py --check`.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_cloud", ROOT / "custom_components" / "miele_lan" / "cloud.py"
)
cloud = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_cloud"] = cloud  # dataclasses need it registered
_spec.loader.exec_module(cloud)


# The countries that were originally extracted from the Android APK. They are
# pinned here because `tools/dump_map_countries.py` reproduces every one of them
# from MAP — if a regeneration ever changes one, that is a real upstream change
# and not a formatting accident.
APK_EXTRACTED = {
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


# ------------------------------------------------------------------ the tables
def test_apk_extracted_ids_unchanged() -> None:
    for cc, client_id in APK_EXTRACTED.items():
        assert cloud.CONSUMER_CLIENT_IDS[cc] == client_id, cc


def test_tables_cover_the_same_countries() -> None:
    assert set(cloud.CONSUMER_CLIENT_IDS) == set(cloud.GIGYA_DC_BY_COUNTRY)


def test_country_codes_are_lowercase_iso_alpha2() -> None:
    for cc in cloud.CONSUMER_CLIENT_IDS:
        assert len(cc) == 2 and cc.islower(), cc


def test_every_gigya_dc_is_one_we_know() -> None:
    assert set(cloud.GIGYA_DC_BY_COUNTRY.values()) <= {"eu1", "us1", "au1"}


def test_regions_that_were_previously_unreachable_are_present() -> None:
    # The countries users reported as missing: issue #6 (sg, be, lv, au).
    for cc in ("sg", "be", "lv", "au"):
        assert cc in cloud.CONSUMER_CLIENT_IDS, cc


# ------------------------------------------------------------ region ordering
@pytest.mark.parametrize("cc,expected_first", [
    ("au", "AS"), ("nz", "AS"), ("sg", "AS"), ("my", "AS"), ("th", "AS"),
    ("de", "EU"), ("gb", "EU"), ("us", "EU"), ("za", "EU"),
])
def test_region_candidates_order(cc: str, expected_first: str) -> None:
    assert cloud.region_candidates(cc)[0] == expected_first


def test_region_candidates_always_covers_both_backends() -> None:
    for cc in cloud.CONSUMER_CLIENT_IDS:
        assert set(cloud.region_candidates(cc)) == {"EU", "AS"}, cc


def test_region_candidates_tolerates_unknown_country() -> None:
    assert cloud.region_candidates("zz") == ["EU", "AS"]


def test_every_candidate_region_resolves_to_a_host() -> None:
    for cc in cloud.CONSUMER_CLIENT_IDS:
        for region in cloud.region_candidates(cc):
            assert region in cloud.REST_HOST_BY_REGION


# ------------------------------------------------------- backend fall-through
HOUSEHOLD = [{"groupId": "ABC123", "groupKey": "DEADBEEF", "devices": [{"fabNr": "1"}]}]


class _FakeResponse:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def text(self) -> str:
        return str(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


class _FakeSession:
    """Answers /V2/GroupKeyId/ per host, and records the order hosts were tried."""

    def __init__(self, by_host: dict) -> None:
        self._by_host = by_host
        self.tried: list[str] = []

    def get(self, url: str, **_kw) -> _FakeResponse:
        host = url.split("/")[2]
        self.tried.append(host)
        status, payload = self._by_host.get(host, (401, []))
        return _FakeResponse(status, payload)


EU_HOST = "rest-eu.domestic.miele-iot.com"
AS_HOST = "rest-as.domestic.miele-iot.com"


@pytest.mark.asyncio
async def test_au_household_found_on_first_try() -> None:
    session = _FakeSession({AS_HOST: (200, HOUSEHOLD)})
    key = await cloud.fetch_groupkey(session, "token", cc="au")
    assert key.group_id == "ABC123"
    assert key.region == "AS"
    assert session.tried == [AS_HOST]  # no needless call to the other backend


@pytest.mark.asyncio
async def test_falls_through_to_the_other_backend() -> None:
    session = _FakeSession({AS_HOST: (401, []), EU_HOST: (200, HOUSEHOLD)})
    key = await cloud.fetch_groupkey(session, "token", cc="au")
    assert key.region == "EU"
    assert session.tried == [AS_HOST, EU_HOST]


@pytest.mark.asyncio
async def test_empty_household_list_is_not_a_match() -> None:
    """A backend that answers 200 with no household isn't the right one."""
    session = _FakeSession({EU_HOST: (200, []), AS_HOST: (200, HOUSEHOLD)})
    key = await cloud.fetch_groupkey(session, "token", cc="de")
    assert key.region == "AS"
    assert session.tried == [EU_HOST, AS_HOST]


@pytest.mark.asyncio
async def test_explicit_region_is_not_second_guessed() -> None:
    session = _FakeSession({EU_HOST: (200, HOUSEHOLD), AS_HOST: (200, HOUSEHOLD)})
    key = await cloud.fetch_groupkey(session, "token", region="EU", cc="au")
    assert key.region == "EU"
    assert session.tried == [EU_HOST]


@pytest.mark.asyncio
async def test_403_aborts_instead_of_trying_the_other_backend() -> None:
    """403 means the token lacks the `mcs` scope — the other host can't fix that."""
    session = _FakeSession({AS_HOST: (403, "forbidden"), EU_HOST: (200, HOUSEHOLD)})
    with pytest.raises(RuntimeError, match="mcs"):
        await cloud.fetch_groupkey(session, "token", cc="au")
    assert session.tried == [AS_HOST]


@pytest.mark.asyncio
async def test_no_backend_has_the_household() -> None:
    session = _FakeSession({})
    with pytest.raises(RuntimeError, match="no household"):
        await cloud.fetch_groupkey(session, "token", cc="au")
    assert session.tried == [AS_HOST, EU_HOST]


# --------------------------------------------------- multi-household accounts
STALE = {"groupId": "AAAABBBBCCCCDDDD", "groupKey": "AAAA", "devices": []}
LIVE = {"groupId": "1111222233334444", "groupKey": "BBBB", "devices": [{"fabNr": "1"}]}


def test_lan_group_wins_over_order() -> None:
    """mDNS is authoritative: the household in front of us is the right one."""
    picked = cloud._pick_household([STALE, LIVE], {"1111222233334444"})
    assert picked["groupId"] == "1111222233334444"


def test_lan_group_wins_even_when_it_lists_no_devices() -> None:
    empty_but_live = {"groupId": "1111222233334444", "groupKey": "B", "devices": []}
    with_devices = {"groupId": "AAAABBBBCCCCDDDD", "groupKey": "A",
                    "devices": [{"fabNr": "9"}]}
    picked = cloud._pick_household(
        [with_devices, empty_but_live], {"1111222233334444"}
    )
    assert picked["groupId"] == "1111222233334444"


def test_falls_back_to_the_household_with_devices() -> None:
    """No mDNS answer (HA on another VLAN): an empty household can't be set up."""
    picked = cloud._pick_household([STALE, LIVE], set())
    assert picked["groupId"] == "1111222233334444"


def test_lan_groups_that_match_nothing_are_ignored() -> None:
    picked = cloud._pick_household([STALE, LIVE], {"0000000000000000"})
    assert picked["groupId"] == "1111222233334444"


def test_single_household_is_returned_unchanged() -> None:
    assert cloud._pick_household([STALE], set())["groupId"] == "AAAABBBBCCCCDDDD"


@pytest.mark.asyncio
async def test_fetch_groupkey_honours_the_lan_group() -> None:
    session = _FakeSession({AS_HOST: (200, [STALE, LIVE])})
    key = await cloud.fetch_groupkey(
        session, "token", cc="au", prefer_group_ids={"1111222233334444"}
    )
    assert key.group_id == "1111222233334444"
    assert key.group_key == "BBBB"
    assert key.region == "AS"


# ------------------------------------------- the household may be on the other
# backend entirely. Captured from the Miele iOS app on 2026-08-22: an `au`
# account authenticates against Gigya's au1 data centre but its household lives
# on rest-eu, while rest-as answers for the same account with an unrelated,
# empty household. Stopping at the first backend that answers picks the wrong
# one — which is exactly what happened on the machine this was found on.
@pytest.mark.asyncio
async def test_keeps_searching_when_the_first_backend_has_the_wrong_household() -> None:
    session = _FakeSession({AS_HOST: (200, [STALE]), EU_HOST: (200, [LIVE])})
    key = await cloud.fetch_groupkey(
        session, "token", cc="au", prefer_group_ids={"1111222233334444"}
    )
    assert key.group_id == "1111222233334444"
    assert key.region == "EU"
    assert session.tried == [AS_HOST, EU_HOST]


@pytest.mark.asyncio
async def test_stops_at_the_first_backend_that_matches() -> None:
    """A match ends the search — no needless call to the other backend."""
    session = _FakeSession({AS_HOST: (200, [LIVE]), EU_HOST: (200, [LIVE])})
    key = await cloud.fetch_groupkey(
        session, "token", cc="au", prefer_group_ids={"1111222233334444"}
    )
    assert key.region == "AS"
    assert session.tried == [AS_HOST]


@pytest.mark.asyncio
async def test_falls_back_to_first_answer_when_nothing_matches() -> None:
    """HA on an isolated VLAN sees no groups; take what we can get."""
    session = _FakeSession({AS_HOST: (200, [STALE]), EU_HOST: (200, [STALE])})
    key = await cloud.fetch_groupkey(
        session, "token", cc="au", prefer_group_ids={"0000000000000000"}
    )
    assert key.group_id == "AAAABBBBCCCCDDDD"
    assert key.region == "AS"
    assert session.tried == [AS_HOST, EU_HOST]


@pytest.mark.asyncio
async def test_no_lan_hint_keeps_the_old_first_answer_wins_behaviour() -> None:
    session = _FakeSession({AS_HOST: (200, [STALE]), EU_HOST: (200, [LIVE])})
    key = await cloud.fetch_groupkey(session, "token", cc="au")
    assert key.group_id == "AAAABBBBCCCCDDDD"
    assert session.tried == [AS_HOST]
