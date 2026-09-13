"""Tests for the laundry spin-speed sensor and the laundry-family extension
of target_temperature (issue #8).

Raw /State body is the WEC395 washing machine fixture posted on the issue —
placeholder appliance, no real fab number / IP / household id involved.
custom_components depends on homeassistant being importable in this venv,
same reasoning as test_minutes.py.
"""

from typing import Any

from custom_components.miele_lan.const import LAUNDRY_FAMILY, OVEN_FAMILY, MieleAppliance
from custom_components.miele_lan.sensor import SENSOR_TYPES, MieleLanSensorDef, _temp_or_none

WEC395_STATE: dict[str, Any] = {
    "Status": 5,
    "ProgramType": 1,
    "ProgramID": 1,
    "ProgramPhase": 260,
    "RemainingTime": [0, 55],
    "StartTime": [0, 0],
    "TargetTemperature": [4000, -32768, -32768],
    "Temperature": [-32768, -32768, -32768],
    "SignalInfo": False,
    "SignalFailure": False,
    "SignalDoor": False,
    "RemoteEnable": [15, 0, 0],
    "ProcessAction": 0,
    "DeviceAction": 0,
    "Light": 0,
    "StandbyState": 1,
    "ElapsedTime": [0, 19],
    "SpinningSpeed": 1600,
}


def _sensor_def(key: str) -> MieleLanSensorDef:
    matches = [d for d in SENSOR_TYPES if d.description.key == key]
    assert len(matches) == 1, f"expected exactly one sensor def for {key!r}, got {len(matches)}"
    return matches[0]


# --- spin_speed ---------------------------------------------------------------

def test_spin_speed_present_returns_rpm_value() -> None:
    d = _sensor_def("spin_speed")
    assert d.description.value_fn(WEC395_STATE) == 1600
    assert d.description.native_unit_of_measurement == "rpm"


def test_spin_speed_required_state_key_gates_missing_field() -> None:
    d = _sensor_def("spin_speed")
    assert d.description.required_state_key == "SpinningSpeed"
    state_without_field = {k: v for k, v in WEC395_STATE.items() if k != "SpinningSpeed"}
    assert d.description.required_state_key not in state_without_field


def test_spin_speed_excludes_pure_dryers() -> None:
    """A tumble dryer has no spin cycle — excluded from `types` outright
    rather than relying solely on required_state_key, which only checks key
    presence and would leave a permanent 0 rpm entity behind if a dryer's
    firmware ever did serialize the field."""
    d = _sensor_def("spin_speed")
    for dryer_type in (
        MieleAppliance.TUMBLE_DRYER,
        MieleAppliance.TUMBLE_DRYER_SEMI_PROFESSIONAL,
        MieleAppliance.DRYER_PROFESSIONAL,
    ):
        assert dryer_type not in d.types


def test_spin_speed_includes_washing_machines_and_washer_dryer() -> None:
    d = _sensor_def("spin_speed")
    for spinning_type in (
        MieleAppliance.WASHING_MACHINE,
        MieleAppliance.WASHING_MACHINE_SEMI_PROFESSIONAL,
        MieleAppliance.WASHING_MACHINE_PROFESSIONAL,
        MieleAppliance.WASHER_DRYER,
    ):
        assert spinning_type in d.types


def test_spin_speed_hidden_when_idle() -> None:
    """Firmware caches the last cycle's spin speed once idle, same as
    remaining/elapsed/start time — gate it the same way."""
    d = _sensor_def("spin_speed")
    idle_state = dict(WEC395_STATE, Status=1)
    assert d.description.value_fn(idle_state) is None


def test_spin_speed_visible_while_running() -> None:
    d = _sensor_def("spin_speed")
    assert d.description.value_fn(WEC395_STATE) == 1600


# --- target_temperature (laundry extension) -----------------------------------

def test_target_temperature_covers_oven_and_laundry_families() -> None:
    d = _sensor_def("target_temperature")
    assert set(OVEN_FAMILY) <= set(d.types)
    assert set(LAUNDRY_FAMILY) <= set(d.types)


def test_laundry_target_temperature_maps_4000_to_40_celsius() -> None:
    d = _sensor_def("target_temperature")
    assert d.description.value_fn(WEC395_STATE) == 40.0


def test_target_temperature_sentinel_yields_none() -> None:
    d = _sensor_def("target_temperature")
    no_target_state = dict(WEC395_STATE, TargetTemperature=[-32768, -32768, -32768])
    assert d.description.value_fn(no_target_state) is None


def test_oven_target_temperature_behaviour_unchanged() -> None:
    d = _sensor_def("target_temperature")
    oven_state = {"Status": 5, "TargetTemperature": [18000, -32768, -32768]}
    assert d.description.value_fn(oven_state) == 180.0


def test_target_temperature_still_gated_when_idle() -> None:
    d = _sensor_def("target_temperature")
    idle_state = dict(WEC395_STATE, Status=1)
    assert d.description.value_fn(idle_state) is None


# --- _temp_or_none sentinel handling (shared by both sensors above) -----------

def test_temp_or_none_returns_none_for_sentinel() -> None:
    assert _temp_or_none([-32768, -32768, -32768], 0) is None


def test_temp_or_none_returns_scaled_value() -> None:
    assert _temp_or_none([4000, -32768, -32768], 0) == 40.0
