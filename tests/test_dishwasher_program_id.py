"""Dishwasher ProgramID mapping (issue #16) — a G7310 owner cycled every
programme on the panel and reported ProgramID 5 rendering as the bare "5"
instead of the panel's "Normal"."""

import json
from pathlib import Path

from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.sensor import _PROGRAM_ID_BY_TYPE, _lookup_for_type

_STRINGS_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "miele_lan" / "strings.json"
)
_TRANSLATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "miele_lan" / "translations"
)


def test_dishwasher_program_id_5_maps_to_normal() -> None:
    state = {"ProgramID": 5}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.DISHWASHER
    ) == "normal"


def test_dishwasher_program_id_unmapped_falls_back_to_number() -> None:
    state = {"ProgramID": 9999}
    assert _lookup_for_type(
        _PROGRAM_ID_BY_TYPE, state, "ProgramID", MieleAppliance.DISHWASHER
    ) == "9999"


def test_normal_state_declared_for_program_id_sensor() -> None:
    strings = json.loads(_STRINGS_PATH.read_text())
    declared = strings["entity"]["sensor"]["program_id"]["state"]
    assert "normal" in declared

    for translation_file in _TRANSLATIONS_DIR.glob("*.json"):
        translated = json.loads(translation_file.read_text())
        declared_translated = translated["entity"]["sensor"]["program_id"]["state"]
        assert "normal" in declared_translated, translation_file.name
