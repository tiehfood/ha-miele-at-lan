"""Power switch state, as the app's SwitchOnAction.IsApplianceOnObservable reads it.

Off for Status 1 (off), 8 (failure) and 255 (not connected); Status 0 only
appears in the all-zero /State some appliances serve on the way to standby.
"""
import pytest

from custom_components.miele_lan.switch import SWITCH_TYPES

POWER = next(d.description for d in SWITCH_TYPES if d.description.key == "power")


@pytest.mark.parametrize("status", [1, 8, 255])
def test_off(status: int) -> None:
    assert POWER.is_on_fn({"Status": status}) is False


@pytest.mark.parametrize("status", [2, 3, 4, 5, 6, 7, 9, 10, 12])
def test_on(status: int) -> None:
    assert POWER.is_on_fn({"Status": status}) is True


@pytest.mark.parametrize("state", [{}, {"Status": None}, {"Status": 0}])
def test_unknown(state: dict) -> None:
    assert POWER.is_on_fn(state) is None
