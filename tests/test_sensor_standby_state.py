"""Tests for the standby_state sensor label table (issue #16).

A dishwasher in deep standby serves an all-zero /State block, including
StandbyState: 0. The decompiled GLOBAL_EnumDeviceStateStandbyState enum
(.claude/memory/standby_state_enum.md) documents 0 as UNKNOWN, not "device is
awake" — rendering it as not_in_standby was a confident false claim.
custom_components depends on homeassistant being importable in this venv,
same reasoning as test_minutes.py.
"""

from typing import Any

from custom_components.miele_lan.sensor import SENSOR_TYPES, MieleLanSensorDef


def _sensor_def(key: str) -> MieleLanSensorDef:
    matches = [d for d in SENSOR_TYPES if d.description.key == key]
    assert len(matches) == 1, f"expected exactly one sensor def for {key!r}, got {len(matches)}"
    return matches[0]


def _state_with_standby(value: int) -> dict[str, Any]:
    return {"StandbyState": value}


def test_standby_state_zero_renders_as_unknown() -> None:
    d = _sensor_def("standby_state")
    assert d.description.value_fn(_state_with_standby(0)) == "unknown"


def test_standby_state_one_renders_as_network_idle() -> None:
    d = _sensor_def("standby_state")
    assert d.description.value_fn(_state_with_standby(1)) == "network_idle"


def test_standby_state_two_renders_as_deep_standby() -> None:
    d = _sensor_def("standby_state")
    assert d.description.value_fn(_state_with_standby(2)) == "deep_standby"


def test_standby_state_three_renders_as_going_to_standby() -> None:
    d = _sensor_def("standby_state")
    assert d.description.value_fn(_state_with_standby(3)) == "going_to_standby"


def test_standby_state_options_cover_every_labelled_value() -> None:
    """ENUM sensors raise if native_value falls outside the declared options
    list — every value STANDBY_STATE_LABELS can produce must be present."""
    d = _sensor_def("standby_state")
    for value in (0, 1, 2, 3):
        assert d.description.value_fn(_state_with_standby(value)) in d.description.options


def test_standby_state_options_has_no_duplicates() -> None:
    """STANDBY_STATE_LABELS now maps 0 to "unknown" itself, so the options
    list must not also append the "unknown" fallback a second time."""
    d = _sensor_def("standby_state")
    options = d.description.options
    assert len(options) == len(set(options))
    assert set(options) == {"unknown", "network_idle", "deep_standby", "going_to_standby"}
