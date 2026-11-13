"""Every programme a G7655 lists in DOP2 2/1584 has a name (G7000 ids, Dop1.3 GLOBAL_PRG_DW_*_G7000RD)."""
import struct
from pathlib import Path

from custom_components.miele_lan.dop2 import _walk_fields
from custom_components.miele_lan.enums import DishWasherProgramId

FIXTURE = Path(__file__).parent / "fixtures" / "g7655_dop2_1584.hex"


def test_every_listed_g7655_programme_is_named() -> None:
    payload = bytes.fromhex(FIXTURE.read_text().strip())
    fields, _ = _walk_fields(payload, 12, struct.unpack_from(">H", payload, 10)[0])
    ids = [i for i in fields[2][1] if i]
    assert ids == [200, 202, 203, 204, 205, 209, 206, 207, 208, 210, 214, 215]
    assert [i for i in ids if i not in DishWasherProgramId] == []


def test_hygiene_and_solar_save() -> None:
    assert DishWasherProgramId[206] == "hygiene"
    assert DishWasherProgramId[208] == "solar_save"
