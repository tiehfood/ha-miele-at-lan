"""H6800BMX reports its own ProgramID numbers for the manual functions.

Values 2, 3, 4, 5, 8 and 15 were read from a H6800BMX while the matching
function was selected on the oven panel. They are not in the shared oven
table and 8 clashes with steam cooking on steam ovens, so they apply to this
model only.
"""
import pytest

from custom_components.miele_lan import enums
from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.coordinator import model_name
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


# Read from a H6860BP while the matching function was selected on the panel.
@pytest.mark.parametrize(
    ("program_id", "name"),
    [(2, "auto_roast"), (3, "gentle_bake"), (4, "full_grill"), (5, "economy_grill"),
     (7, "fan_plus"), (8, "intensive_bake"), (9, "top_heat"),
     (10, "conventional_heat"), (12, "bottom_heat"), (13, "fan_grill"),
     (19, "moisture_plus")],
)
def test_h6860bp_heating_functions(program_id, name):
    assert _name(program_id, "H6860BP") == name


def test_other_models_keep_the_shared_table():
    assert _name(2, "H7660BP") == "2"
    assert _name(3, "H7660BP") == "3"
    assert _name(4, "H7660BP") == "4"
    assert _name(5, "H7660BP") == "5"
    assert _name(8, "H7660BP") == "8"
    assert _name(13, "H7660BP") == "fan_plus"
    assert _name(13, "H6800BMX") == "fan_plus"
    assert _name(7, "H7660BP") == "auto_roast"
    assert _name(7, "H6800BMX") == "auto_roast"
    assert _name(9, "H7660BP") == "grill"
    assert _name(10, "H7660BP") == "full_grill"
    assert _name(12, "H7660BP") == "12"


def test_names_are_translated():
    import json
    from pathlib import Path
    states = json.loads(
        (Path(enums.__file__).parent / "strings.json").read_text()
    )["entity"]["sensor"]["program_id"]["state"]
    for name in enums.H6800BMXProgramId.values():
        assert name in states


def test_h6860bp_names_are_translated():
    # Every name must exist as a translated state of the program sensors,
    # otherwise the frontend shows the raw key.
    import json
    from pathlib import Path

    root = Path(__file__).parents[1] / "custom_components/miele_lan"
    for file in ["strings.json", "translations/en.json", "translations/de.json"]:
        sensors = json.loads((root / file).read_text(encoding="utf-8"))["entity"]["sensor"]
        for key in ("program_id", "program_id_raw"):
            missing = set(enums.H6860BPProgramId.values()) - set(sensors[key]["state"])
            assert not missing, f"{file} {key}: {missing}"


# Read from a DGC 6860 D steam combi oven while the matching function ran.
# The TechType has spaces; the lookup key is the TechType without them.
@pytest.mark.parametrize(
    ("program_id", "name"),
    [(1, "fan_plus"), (2, "cake_plus"), (3, "conventional_heat"), (4, "top_heat"),
     (5, "bottom_heat"), (6, "full_grill"), (7, "economy_grill"), (8, "fan_grill"),
     (9, "intensive_bake"), (13, "steam_cooking"), (17, "gentle_bake"),
     (29, "sous_vide"), (61, "combination_mode")],
)
def test_dgc6860d_functions(program_id, name):
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, {"ProgramID": program_id}, "ProgramID",
        MieleAppliance.STEAM_OVEN_COMBI,
        _MODEL_PROGRAM_ID.get(model_name({"tech_type": "DGC 6860 D"})),
    ) == name


def test_other_steam_combi_ovens_keep_1_as_defrost():
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, {"ProgramID": 1}, "ProgramID",
        MieleAppliance.STEAM_OVEN_COMBI, None,
    ) == "defrost"
