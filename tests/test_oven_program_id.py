"""H6800BMX reports its own ProgramID numbers for the manual functions.

Values 2, 3, 4, 5, 8 and 15 were read from a H6800BMX while the matching
function was selected on the oven panel. They are not in the shared oven
table and 8 clashes with steam cooking on steam ovens, so they apply to this
model only.
"""
import pytest

from custom_components.miele_lan import enums
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.sensor import (
    _MODEL_PROGRAM_ID,
    _PROGRAM_ID_BY_TYPE,
    _lookup_for_type,
)


def _name(program_id, model):
    return _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, {"ProgramID": program_id}, "ProgramID",
        MieleAppliance.OVEN, _MODEL_PROGRAM_ID.get(model),
    )


@pytest.mark.parametrize(
    ("program_id", "name"),
    [(2, "auto_roast"), (3, "grill"), (4, "fan_plus"),
     (5, "intensive_bake"), (8, "fan_grill"), (15, "browning")],
)
def test_h6800bmx_manual_functions(program_id, name):
    assert _name(program_id, "H6800BMX") == name


def test_other_models_keep_the_shared_table():
    assert _name(8, "H7660BP") == "8"
    assert _name(13, "H7660BP") == "fan_plus"
    assert _name(13, "H6800BMX") == "fan_plus"


def test_names_are_translated():
    import json
    from pathlib import Path
    states = json.loads(
        (Path(enums.__file__).parent / "strings.json").read_text()
    )["entity"]["sensor"]["program_id"]["state"]
    for name in enums.H6800BMXProgramId.values():
        assert name in states
