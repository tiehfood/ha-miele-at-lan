"""Tests for the push-before-coordinator-exists log-level decision.

`_push_unmatched_fab_action` lives in `__init__.py`, same as
`_merge_mdns_adoptions` in test_mdns_adoption.py — pure/sync and stubbable
without touching HA's config-entry machinery.
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan import _push_unmatched_fab_action  # noqa: E402

FAB_EXPECTED = "000000000001"
FAB_UNKNOWN = "000000000099"


def test_expected_fab_during_setup_is_debug_and_ignored() -> None:
    action, level = _push_unmatched_fab_action(
        {FAB_EXPECTED}, False, FAB_EXPECTED,
    )

    assert action == "setup_in_progress"
    assert level == logging.DEBUG


def test_expected_fab_after_setup_completes_is_a_warning() -> None:
    action, level = _push_unmatched_fab_action(
        {FAB_EXPECTED}, True, FAB_EXPECTED,
    )

    assert action == "unknown"
    assert level == logging.WARNING


def test_unknown_fab_during_setup_is_still_a_warning() -> None:
    action, level = _push_unmatched_fab_action(
        {FAB_EXPECTED}, False, FAB_UNKNOWN,
    )

    assert action == "unknown"
    assert level == logging.WARNING


def test_unknown_fab_after_setup_completes_is_a_warning() -> None:
    action, level = _push_unmatched_fab_action(
        {FAB_EXPECTED}, True, FAB_UNKNOWN,
    )

    assert action == "unknown"
    assert level == logging.WARNING
