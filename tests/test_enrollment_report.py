"""Tests for the enrollment summary/repair-issue formatting helpers.

`enrollment_report.py` is loaded straight off disk, same as
`options_validation.py` in test_options_validation.py — it has zero
relative imports, so this stays free of any `homeassistant` dependency.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_enrollment_report",
    ROOT / "custom_components" / "miele_lan" / "enrollment_report.py",
)
er = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_enrollment_report"] = er
_spec.loader.exec_module(er)


FAB = "000000000000"
OTHER_FAB = "000000000001"
REASON = "signed verify failed — wrong key, appliance offline, or not paired into this household"
OTHER_REASON = "no LAN IP found (mDNS did not answer and no static IP is configured)"


# --------------------------------------------------------------- summary_line
def test_summary_line_all_enrolled_has_no_missing_section() -> None:
    line = er.summary_line(3, [])
    assert line == "enrolled 3 of 3 appliances"
    assert "missing" not in line


def test_summary_line_zero_of_zero() -> None:
    assert er.summary_line(0, []) == "enrolled 0 of 0 appliances"


def test_summary_line_one_missing_names_fab_and_reason() -> None:
    failed = [er.FailedDevice(fab=FAB, reason=REASON)]
    line = er.summary_line(2, failed)
    assert line == f"enrolled 2 of 3 appliances — missing {FAB} ({REASON})"


def test_summary_line_multiple_missing_are_semicolon_joined() -> None:
    failed = [
        er.FailedDevice(fab=FAB, reason=REASON),
        er.FailedDevice(fab=OTHER_FAB, reason=OTHER_REASON),
    ]
    line = er.summary_line(1, failed)
    assert line == (
        f"enrolled 1 of 3 appliances — missing "
        f"{FAB} ({REASON}); {OTHER_FAB} ({OTHER_REASON})"
    )


# ------------------------------------------------- issue_translation_placeholders
def test_placeholders_empty_when_nothing_failed() -> None:
    placeholders = er.issue_translation_placeholders([])
    assert placeholders == {"count": "0", "devices": ""}


def test_placeholders_count_matches_failed_list_length() -> None:
    failed = [
        er.FailedDevice(fab=FAB, reason=REASON),
        er.FailedDevice(fab=OTHER_FAB, reason=OTHER_REASON),
    ]
    placeholders = er.issue_translation_placeholders(failed)
    assert placeholders["count"] == "2"


def test_placeholders_devices_lists_fab_and_reason_per_line() -> None:
    failed = [
        er.FailedDevice(fab=FAB, reason=REASON),
        er.FailedDevice(fab=OTHER_FAB, reason=OTHER_REASON),
    ]
    placeholders = er.issue_translation_placeholders(failed)
    assert placeholders["devices"] == f"{FAB}: {REASON}\n{OTHER_FAB}: {OTHER_REASON}"
