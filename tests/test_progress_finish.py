"""Programme progress and finish time, computed the way the app does.

Laundry GetProgress (WMV960.cs:7851) and the oven ProgressSource: elapsed /
(elapsed + remaining), full at programme end. Finish time is now + delayed
start + remaining, as the laundry EndTime (WMV960.cs:5177).
"""
import datetime as dt
from types import SimpleNamespace

import pytest

import custom_components.miele_lan.sensor as sensor_mod
from custom_components.miele_lan.const import CYCLE_FAMILY

NOW = dt.datetime(2026, 11, 13, 8, 0, 30, tzinfo=dt.timezone.utc)


def _desc(key: str):
    defs = [d for d in sensor_mod.SENSOR_TYPES if d.description.key == key]
    assert len(defs) == 1
    assert set(defs[0].types) == set(CYCLE_FAMILY)
    return defs[0].description


@pytest.fixture(autouse=True)
def _now(monkeypatch):
    monkeypatch.setattr(sensor_mod, "_utcnow", lambda: NOW)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"Status": 5, "ElapsedTime": [1, 30], "RemainingTime": [0, 30]}, 75),
        ({"Status": 6, "ElapsedTime": 10, "RemainingTime": 30}, 25),
        ({"Status": 7, "ElapsedTime": [0, 0], "RemainingTime": [0, 0]}, 100),
        ({"Status": 5, "ElapsedTime": [0, 0], "RemainingTime": [0, 0]}, None),
        ({"Status": 4, "ElapsedTime": [0, 0], "RemainingTime": [2, 0]}, None),
        ({"Status": 1, "ElapsedTime": [1, 0], "RemainingTime": [0, 0]}, None),
    ],
)
def test_progress(state: dict, expected: int | None) -> None:
    assert _desc("progress").value_fn(state) == expected


def test_finish_time_running() -> None:
    state = {"Status": 5, "RemainingTime": [1, 15], "StartTime": [0, 0]}
    assert _desc("finish_time").value_fn(state) == dt.datetime(
        2026, 11, 13, 9, 15, tzinfo=dt.timezone.utc
    )


def test_finish_time_includes_the_delayed_start() -> None:
    state = {"Status": 4, "RemainingTime": [2, 0], "StartTime": [3, 0]}
    assert _desc("finish_time").value_fn(state) == dt.datetime(
        2026, 11, 13, 13, 0, tzinfo=dt.timezone.utc
    )


@pytest.mark.parametrize(
    "state",
    [
        {"Status": 7, "RemainingTime": [0, 0]},
        {"Status": 1, "RemainingTime": [1, 0]},
        {"Status": 5},
    ],
)
def test_no_finish_time(state: dict) -> None:
    assert _desc("finish_time").value_fn(state) is None


def _clock(monkeypatch, hh: int, mm: int, ss: int) -> None:
    now = dt.datetime(2026, 11, 16, hh, mm, ss, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(sensor_mod, "_utcnow", lambda: now)


def _finish_entity(state: dict):
    coord = SimpleNamespace(client=SimpleNamespace(route="dish"), data=SimpleNamespace(state=state))
    return sensor_mod.MieleLanFinishTimeSensor(coord, _desc("finish_time")), coord


def _at(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(2026, 11, 16, hh, mm, tzinfo=dt.timezone.utc)


def test_finish_time_holds_through_the_minute_wobble(monkeypatch) -> None:
    """The G7310 timeline from #76: the raw value alternates 18:45 and 18:46."""
    entity, coord = _finish_entity({})
    seen = []
    for (hh, mm, ss), remaining in [
        ((17, 21, 37), 84), ((17, 22, 7), 84), ((17, 22, 57), 83),
        ((17, 23, 7), 83), ((17, 24, 7), 82), ((17, 24, 50), 81),
    ]:
        _clock(monkeypatch, hh, mm, ss)
        coord.data.state = {"Status": 5, "RemainingTime": [0, remaining], "StartTime": [0, 0]}
        seen.append(entity.native_value)
    assert seen == [_at(18, 45)] * 6


def test_finish_time_follows_a_real_change(monkeypatch) -> None:
    entity, coord = _finish_entity({"Status": 5, "RemainingTime": [0, 84]})
    _clock(monkeypatch, 17, 21, 37)
    assert entity.native_value == _at(18, 45)
    coord.data.state = {"Status": 5, "RemainingTime": [0, 90]}
    assert entity.native_value == _at(18, 51)


def test_finish_time_starts_fresh_after_the_programme(monkeypatch) -> None:
    entity, coord = _finish_entity({"Status": 5, "RemainingTime": [0, 84]})
    _clock(monkeypatch, 17, 21, 37)
    assert entity.native_value == _at(18, 45)
    coord.data.state = {"Status": 1, "RemainingTime": [0, 0]}
    assert entity.native_value is None
    _clock(monkeypatch, 17, 22, 7)
    coord.data.state = {"Status": 5, "RemainingTime": [0, 84]}
    assert entity.native_value == _at(18, 46)

