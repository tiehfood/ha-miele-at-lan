"""A push channel that went quiet mid-programme must not keep the slow poll.

push_mode stays "push:active" after the first push, so the adaptive cadence
also needs the last push to be recent while a programme runs.
"""
from datetime import timedelta

import pytest

import custom_components.miele_lan.coordinator as coordinator_mod
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData
from custom_components.miele_lan.enrollment import EnrolledDevice
from custom_components.miele_lan.polling import (
    POLL_ACTIVE_INTERVAL,
    POLL_FALLBACK_INTERVAL,
    PUSH_STALE_AFTER,
)

NOW = 1_000_000.0


@pytest.fixture
def coord(monkeypatch):
    monkeypatch.setattr(coordinator_mod.time, "time", lambda: NOW)
    coord = object.__new__(MieleLanCoordinator)
    coord.fab = "000000000001"
    coord.enrollment = EnrolledDevice(
        fab=coord.fab, host_ip="", route=coord.fab, supervision_ok=True,
        subscriptions_ok=["/State/"], enrolled_at=0.0,
    )
    coord._push_count = 5
    coord._data = MieleLanData(state={"Status": 5})
    coord.update_interval = timedelta(seconds=POLL_FALLBACK_INTERVAL)
    return coord


def test_recent_push_keeps_the_slow_poll(coord) -> None:
    coord._last_push_at = NOW - 30
    coord._apply_adaptive_poll_interval()
    assert coord.update_interval.total_seconds() == POLL_FALLBACK_INTERVAL


def test_quiet_push_channel_mid_programme_polls_fast(coord) -> None:
    coord._last_push_at = NOW - PUSH_STALE_AFTER - 1
    coord._apply_adaptive_poll_interval()
    assert coord.update_interval.total_seconds() == POLL_ACTIVE_INTERVAL


def test_quiet_push_channel_while_idle_stays_slow(coord) -> None:
    coord._last_push_at = NOW - PUSH_STALE_AFTER - 1
    coord._data.state = {"Status": 1}
    coord._apply_adaptive_poll_interval()
    assert coord.update_interval.total_seconds() == POLL_FALLBACK_INTERVAL
