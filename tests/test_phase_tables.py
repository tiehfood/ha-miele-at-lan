"""Phase tables cover the app's GLOBAL_PHASE_DW_* (1792-1811) and GLOBAL_PHASE_TD_* (512-539)."""
from custom_components.miele_lan.enums import (
    ProgramPhaseDishwasher,
    ProgramPhaseTumbleDryer,
    ProgramPhaseWasherDryer,
)


def test_every_dishwasher_phase_is_named() -> None:
    assert [p for p in range(1792, 1812) if p not in ProgramPhaseDishwasher] == []


def test_dishwasher_tail_phases() -> None:
    assert ProgramPhaseDishwasher[1807] == "post_rinse"
    assert ProgramPhaseDishwasher[1810] == "fan_run"
    assert ProgramPhaseDishwasher[1811] == "auto_open"


def test_every_dryer_phase_is_named() -> None:
    for table in (ProgramPhaseTumbleDryer, ProgramPhaseWasherDryer):
        assert [p for p in range(512, 540) if p not in table] == []
        assert table[525] == "hygiene"
