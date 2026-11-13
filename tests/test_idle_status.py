"""Status values (GLOBAL_DS_MAINSTATEEXTERN, Dop1.3) that hide stale programme data."""
import pytest

from custom_components.miele_lan.const import is_idle_state
from custom_components.miele_lan.enums import StateStatus
from custom_components.miele_lan.polling import POLL_FALLBACK_INTERVAL, decide_poll_interval


@pytest.mark.parametrize("status", [1, 2, 8, 10, 12, 144, 145, 255])
def test_idle(status: int) -> None:
    assert is_idle_state({"Status": status}) is True


@pytest.mark.parametrize("status", [3, 4, 5, 6, 7, 9])
def test_not_idle(status: int) -> None:
    assert is_idle_state({"Status": status}) is False


def test_idle_appliance_without_push_keeps_the_slow_poll() -> None:
    interval = decide_poll_interval(is_push_active=False, is_idle=is_idle_state({"Status": 10}))
    assert interval == POLL_FALLBACK_INTERVAL


def test_default_and_locked_are_labelled() -> None:
    assert StateStatus[144] == "default"
    assert StateStatus[145] == "locked"
