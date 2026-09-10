"""Tests for the start/stop/pause/resume button-to-appliance mapping.

`STOP_PROCESS_FAMILY` (const.py) is pure and needs no Home Assistant.
`button.BUTTONS` itself imports `homeassistant.components.button`, so it
still runs without an `hass` instance — same reasoning as
test_switch_power_gate.py, just one layer up.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.const import (  # noqa: E402
    CYCLE_FAMILY,
    DISHWASHER_FAMILY,
    LAUNDRY_FAMILY,
    OVEN_FAMILY,
    STOP_PROCESS_FAMILY,
    MieleAppliance,
)


def test_stop_process_family_excludes_every_oven() -> None:
    assert set(STOP_PROCESS_FAMILY).isdisjoint(OVEN_FAMILY)


def test_stop_process_family_is_laundry_plus_dishwasher() -> None:
    assert set(STOP_PROCESS_FAMILY) == set(LAUNDRY_FAMILY) | set(DISHWASHER_FAMILY)


def test_stop_process_family_is_a_subset_of_cycle_family() -> None:
    assert set(STOP_PROCESS_FAMILY) <= set(CYCLE_FAMILY)


def test_no_appliance_gets_two_stop_buttons() -> None:
    from custom_components.miele_lan import button

    for device_type in MieleAppliance:
        if device_type is MieleAppliance.UNKNOWN:
            continue
        stop_keys = [
            d.description.key
            for d in button.BUTTONS
            if device_type in d.types and "stop" in d.description.key
        ]
        assert len(stop_keys) <= 1, f"{device_type} would get two stop buttons: {stop_keys}"


def test_oven_keeps_dop2_stop_program_only() -> None:
    from custom_components.miele_lan import button

    keys_for_oven = {
        d.description.key for d in button.BUTTONS if MieleAppliance.OVEN in d.types
    }
    assert "stop_program" in keys_for_oven
    assert "stop_process" not in keys_for_oven


def test_dishwasher_and_laundry_get_stop_process_not_stop_program() -> None:
    from custom_components.miele_lan import button

    for device_type in (MieleAppliance.DISHWASHER, MieleAppliance.WASHING_MACHINE):
        keys = {d.description.key for d in button.BUTTONS if device_type in d.types}
        assert "stop_process" in keys
        assert "stop_program" not in keys


def test_start_pause_resume_stay_on_full_cycle_family() -> None:
    from custom_components.miele_lan import button

    for key in ("start_process", "pause_process", "resume_process"):
        (matching_def,) = [d for d in button.BUTTONS if d.description.key == key]
        assert set(matching_def.types) == set(CYCLE_FAMILY)
