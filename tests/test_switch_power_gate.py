"""Tests for the hood power-switch gate (issue #39).

Pure-Python — `wants_power_switch` has no Home Assistant imports, so this
stays free of any `homeassistant` dependency, same as test_polling.py.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.const import (  # noqa: E402
    MieleAppliance,
    wants_power_switch,
)


def test_dop1_hood_gets_no_power_switch() -> None:
    """The fan entity already covers on/off; DOP2 write 404s on these hoods."""
    assert wants_power_switch(MieleAppliance.HOOD, hood_dop1_supported=True) is False


def test_non_dop1_hood_keeps_power_switch() -> None:
    """No fan entity and no evidence the DOP2 write fails here — keep it."""
    assert wants_power_switch(MieleAppliance.HOOD, hood_dop1_supported=False) is True


def test_other_powerable_appliances_unaffected() -> None:
    for device_type in (
        MieleAppliance.OVEN,
        MieleAppliance.DISHWASHER,
        MieleAppliance.DISH_WARMER,
        MieleAppliance.COFFEE_SYSTEM,
    ):
        assert wants_power_switch(device_type, hood_dop1_supported=True) is True
        assert wants_power_switch(device_type, hood_dop1_supported=False) is True


def test_non_powerable_appliance_gets_no_switch() -> None:
    assert wants_power_switch(MieleAppliance.FRIDGE, hood_dop1_supported=False) is False
