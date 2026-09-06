"""Tests for the adaptive poll-interval decision.

`polling.py` is loaded straight off disk, same as `enrollment_report.py` in
test_enrollment_report.py — it has zero relative imports, so this stays free
of any `homeassistant` dependency.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_polling",
    ROOT / "custom_components" / "miele_lan" / "polling.py",
)
polling = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_polling"] = polling
_spec.loader.exec_module(polling)


def test_active_and_push_silent_polls_fast() -> None:
    interval = polling.decide_poll_interval(is_push_active=False, is_idle=False)
    assert interval == polling.POLL_ACTIVE_INTERVAL


def test_idle_and_push_silent_polls_slow() -> None:
    interval = polling.decide_poll_interval(is_push_active=False, is_idle=True)
    assert interval == polling.POLL_FALLBACK_INTERVAL


def test_active_and_push_active_polls_slow() -> None:
    """A push-capable appliance never speeds up, even mid-programme."""
    interval = polling.decide_poll_interval(is_push_active=True, is_idle=False)
    assert interval == polling.POLL_FALLBACK_INTERVAL


def test_idle_and_push_active_polls_slow() -> None:
    interval = polling.decide_poll_interval(is_push_active=True, is_idle=True)
    assert interval == polling.POLL_FALLBACK_INTERVAL


def test_active_interval_is_meaningfully_faster_but_not_aggressive() -> None:
    """Guards against re-introducing a sub-5s cadence (see issue #18):
    every fast poll is a signed request the appliance has to decrypt."""
    assert polling.POLL_ACTIVE_INTERVAL < polling.POLL_FALLBACK_INTERVAL
    assert polling.POLL_ACTIVE_INTERVAL >= 5
