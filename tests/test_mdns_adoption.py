"""Tests for adopting mDNS-confirmed appliances the cloud device list omits.

`_merge_mdns_adoptions` lives in `__init__.py`; `enroll_all`'s missing-fab
warning lives in `enrollment.py`. Both are pure/async-stubbable without
touching HA's config-entry machinery.
"""

import asyncio
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import _merge_mdns_adoptions  # noqa: E402
from custom_components.miele_lan.enrollment import enroll_all  # noqa: E402

FAB_LISTED = "000000000001"
FAB_LISTED_LEGACY_KEY = "000000000002"
FAB_UNLISTED = "000000000003"


def _mdns_hit(fab: str, device_type: int = 0) -> dict:
    return {"fabNr": fab, "deviceType": device_type, "deviceName": "",
            "host": "203.0.113.1", "hostname": "Miele-example.local."}


# ------------------------------------------------------- _merge_mdns_adoptions
def test_adopts_only_the_fab_missing_from_a_partial_cloud_list() -> None:
    devices = [{"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""}]
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_UNLISTED)]

    merged, adopted = _merge_mdns_adoptions(devices, mdns_results)

    assert [d["fabNr"] for d in adopted] == [FAB_UNLISTED]
    assert {d.get("fabNr") or d.get("fab") for d in merged} == {
        FAB_LISTED, FAB_UNLISTED,
    }
    assert len(merged) == 2


def test_recognises_fab_under_either_fabnr_or_fab_key_and_does_not_duplicate() -> None:
    devices = [
        {"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""},
        {"fab": FAB_LISTED_LEGACY_KEY},
    ]
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_LISTED_LEGACY_KEY)]

    merged, adopted = _merge_mdns_adoptions(devices, mdns_results)

    assert adopted == []
    assert len(merged) == 2


def test_empty_cloud_list_still_adopts_everything_mdns_found() -> None:
    mdns_results = [_mdns_hit(FAB_LISTED), _mdns_hit(FAB_UNLISTED)]

    merged, adopted = _merge_mdns_adoptions([], mdns_results)

    assert {d["fabNr"] for d in adopted} == {FAB_LISTED, FAB_UNLISTED}
    assert merged == adopted


def test_mdns_finding_nothing_changes_nothing() -> None:
    devices = [{"fabNr": FAB_LISTED, "deviceType": 1, "deviceName": ""}]

    merged, adopted = _merge_mdns_adoptions(devices, [])

    assert adopted == []
    assert merged == devices


# --------------------------------------------------------------- enroll_all
class _NeverCalledResolver:
    zeroconf = None

    async def resolve(self, fab: str) -> str | None:
        raise AssertionError(f"resolve() should not be called for {fab!r}")


def test_enroll_all_warns_and_skips_worklist_entry_with_no_fab_key(
    caplog,
) -> None:
    devices = [{"deviceType": 1, "deviceName": "no fab here"}]

    with caplog.at_level(logging.WARNING):
        result = asyncio.run(enroll_all(
            devices,
            group_id_hex="00" * 16,
            group_key_hex="00" * 64,
            ha_fab="000000000000",
            ha_hostname="ha.local.",
            ha_port=12345,
            resolver=_NeverCalledResolver(),
        ))

    assert result.enrolled == []
    assert result.failed == []
    assert any(
        record.levelno == logging.WARNING and "no fab number" in record.getMessage()
        for record in caplog.records
    )
