"""Tests for the UpdateFailed message-naming helper.

`_describe_update_error` lives in `coordinator.py`, same as
`_merge_mdns_adoptions` in test_mdns_adoption.py for `__init__.py` — pure and
stubbable without touching HA's config-entry machinery.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.coordinator import _describe_update_error  # noqa: E402


def test_exception_with_empty_str_names_the_type() -> None:
    assert _describe_update_error(TimeoutError()) == "TimeoutError"


def test_exception_with_a_message_includes_type_and_message() -> None:
    err = ConnectionError("HTTP error 503: service unavailable")

    assert (
        _describe_update_error(err)
        == "ConnectionError: HTTP error 503: service unavailable"
    )
