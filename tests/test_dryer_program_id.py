"""Tumble dryer ProgramID 25 mapping.

A TMV840WP set to Cottons (drying degree cupboard dry) reports ProgramID 25
in /State, which used to render as the bare "25" instead of a programme name.
"""
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.sensor import _PROGRAM_ID_BY_TYPE, _lookup_for_type


def test_dryer_program_id_25_is_cottons() -> None:
    state = {"ProgramID": 25}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.TUMBLE_DRYER
    ) == "cottons"


def test_dryer_unmapped_program_id_falls_back_to_number() -> None:
    state = {"ProgramID": 9999}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.TUMBLE_DRYER
    ) == "9999"
