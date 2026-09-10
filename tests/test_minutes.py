"""Tests for _minutes(), which converts /State.RemainingTime, .ElapsedTime
and .StartTime into total minutes.

Different appliances send this field in different shapes: some as
[hours, minutes], others as a plain int of minutes. custom_components
depends on homeassistant being importable in this venv, so the module is
imported directly rather than loaded off disk.
"""

import pytest

from custom_components.miele_lan.sensor import _minutes


@pytest.mark.parametrize(
    "minutes",
    [10, 79, 119, 73, 44, 69],
)
def test_plain_int_minutes(minutes: int) -> None:
    assert _minutes(minutes) == minutes


def test_zero_minutes_is_not_none() -> None:
    assert _minutes(0) == 0


def test_hours_minutes_list_still_works() -> None:
    assert _minutes([1, 30]) == 90


@pytest.mark.parametrize("value", [True, False])
def test_bool_rejected(value: bool) -> None:
    assert _minutes(value) is None


@pytest.mark.parametrize(
    "field",
    [
        None,
        "10",
        [1],
        [1, 2, 3],
        [1, "2"],
        ["1", 2],
    ],
)
def test_invalid_shapes_return_none(field) -> None:
    assert _minutes(field) is None


@pytest.mark.parametrize("value", [-1, -32768])
def test_negative_minutes_return_none(value: int) -> None:
    assert _minutes(value) is None
