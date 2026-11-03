"""Smart Grid binary sensor, the second RemoteEnable slot.

Checked on a WMV960 washer by switching Smart Grid off and on in its menu.
/State reported RemoteEnable [15, 0, 0] while it was off and [15, 1, 0] while
it was on. A dishwasher (G6992) and an oven (H6800BMX) report [15, 0, 0]
because neither has a Smart Grid setting. Turning the dishwasher's SmartStart
on did not change the slot.
"""
import pytest

from custom_components.miele_lan.binary_sensor import BINARY_SENSOR_TYPES
from custom_components.miele_lan.const import LAUNDRY_FAMILY, MieleAppliance


def _smart_grid():
    matches = [d for d in BINARY_SENSOR_TYPES if d.description.key == "smart_grid"]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("remote_enable,expected", [
    ([15, 1, 0], True),
    ([15, 0, 0], False),
    ([15, 1, 1], True),
    ([15, 0, 1], False),
])
def test_smart_grid_follows_the_second_slot(remote_enable, expected):
    assert _smart_grid().description.is_on_fn({"RemoteEnable": remote_enable}) is expected


def test_smart_grid_is_off_when_remote_enable_is_missing():
    assert _smart_grid().description.is_on_fn({}) is False


def test_smart_grid_is_laundry_only():
    d = _smart_grid()
    assert set(d.types) == set(LAUNDRY_FAMILY)
    assert MieleAppliance.DISHWASHER not in d.types
    assert MieleAppliance.OVEN not in d.types
