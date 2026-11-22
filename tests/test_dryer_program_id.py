"""Tumble dryer ProgramID 25 mapping.

A TMV840WP set to Cottons (drying degree cupboard dry) reports ProgramID 25
in /State, and 26 with the Eco extra switched on. Both used to render as the
bare number instead of a programme name.
"""
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.sensor import (
    _MODEL_PROGRAM_ID,
    _PROGRAM_ID_BY_TYPE,
    _lookup_for_type,
)


def test_dryer_program_id_25_is_cottons() -> None:
    state = {"ProgramID": 25}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.TUMBLE_DRYER
    ) == "cottons"


def test_dryer_program_id_26_is_cottons_eco() -> None:
    state = {"ProgramID": 26}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.TUMBLE_DRYER
    ) == "cottons_eco"


def test_dryer_unmapped_program_id_falls_back_to_number() -> None:
    state = {"ProgramID": 9999}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.TUMBLE_DRYER
    ) == "9999"


# Read from a TMR840WP while the matching programme ran.
def test_tmr840wp_program_id_31_is_minimum_iron() -> None:
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, {"ProgramID": 31}, "ProgramID",
        MieleAppliance.TUMBLE_DRYER, _MODEL_PROGRAM_ID.get("TMR840WP"),
    ) == "minimum_iron"


def test_other_dryers_keep_31_as_bed_linen() -> None:
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, {"ProgramID": 31}, "ProgramID",
        MieleAppliance.TUMBLE_DRYER, _MODEL_PROGRAM_ID.get("TMV840WP"),
    ) == "bed_linen"
