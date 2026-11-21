"""Lifetime energy/water consumption from DOP2 2/1585 (DWTDWM attrs 5/6).

Fixtures captured live from a G7655 (XKM EK057S, FW 09.14, protocolVersion 4).
Cross-checked against the Miele app's usage pages (the same figures the
`/V2/Devices/{fab}/Statistics/` cloud endpoint serves):

- 2026-10-03 capture (g7655_dop2_1585.hex): 182600 Wh / 2447 L; the cloud's
  lifetime usage page tracked that to within ~0.1 % at that date.
- 2026-10-24 capture (g7655_dop2_1585_20261024.hex): 197200 Wh / 2643 L;
  cloud endpoint earlier that day served 193.4 kWh / 2494 L (the module's
  own counter accrues slightly differently from the cloud's buckets).

jkaberg's G7197 captures from issue #59 (206600 Wh / 2789 L, IntType 121/44)
pin a second model. The IntType guard means a model reporting a different
unit (e.g. 45 GLOBAL_VALINT_ECO_ENERGY_KWH) yields None, never a wrong
number.
"""

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.const import MieleAppliance  # noqa: E402
from custom_components.miele_lan.dop2 import (  # noqa: E402
    _lifetime_total,
    parse_global_device_context,
)
from custom_components.miele_lan.sensor import DEVICE_CONTEXT_SENSORS  # noqa: E402

F1 = Path(__file__).parent / "fixtures" / "g7655_dop2_1585.hex"
F2 = Path(__file__).parent / "fixtures" / "g7655_dop2_1585_20261024.hex"
G1 = Path(__file__).parent / "fixtures" / "g7197_dop2_1585_door_closed.hex"


def _ctx(data: bytes) -> dict[str, Any]:
    return parse_global_device_context(data)


def test_oct3_fixture_decodes_lifetime_totals() -> None:
    ctx = _ctx(bytes.fromhex(F1.read_text().replace("\n", "")))
    # Cross-checked against the cloud usage page (~182.6 kWh / 2447 L).
    assert ctx["lifetime_energy_wh"] == 182600
    assert ctx["lifetime_water_l"] == 2447


def test_oct24_fixture_decodes_lifetime_totals() -> None:
    ctx = _ctx(bytes.fromhex(F2.read_text().replace("\n", "")))
    assert ctx["lifetime_energy_wh"] == 197200
    assert ctx["lifetime_water_l"] == 2643
    # Totals only ever accumulate: the newer capture must be >= the older.
    assert ctx["lifetime_energy_wh"] >= _ctx(bytes.fromhex(F1.read_text()))["lifetime_energy_wh"]
    assert ctx["lifetime_water_l"] >= _ctx(bytes.fromhex(F1.read_text()))["lifetime_water_l"]


def test_g7197_fixture_decodes_lifetime_totals() -> None:
    """Second model (issue #59, jkaberg's captures): same layout, IntType 121/44."""
    ctx = _ctx(bytes.fromhex(G1.read_text().replace("\n", "")))
    assert ctx["lifetime_energy_wh"] == 206600
    assert ctx["lifetime_water_l"] == 2789


def test_lifetime_total_rejects_unexpected_inttype() -> None:
    """A model reporting kWh (IntType 45) must not be read as Wh or L."""
    ctx = _ctx(bytes.fromhex(F1.read_text().replace("\n", "")))
    da = dict(ctx["device_attributes_dwtdwm"])
    for attr, int_type in ((5, 121), (6, 44)):
        m = da[attr]
        sub = dict(m[1])
        sub[6] = (sub[6][0], 45)  # pretend the firmware reports kWh
        da[attr] = (m[0], sub)
        assert _lifetime_total(da, attr, int_type) is None
    # And the real IntTypes still pass straight from the same fixtures.
    assert _lifetime_total(dict(ctx["device_attributes_dwtdwm"]), 5, 121) == 182600
    assert _lifetime_total(dict(ctx["device_attributes_dwtdwm"]), 6, 44) == 2447


def _dw_sensor(key: str) -> Any:
    matches = [d for d in DEVICE_CONTEXT_SENSORS if d.description.key == key]
    assert matches, f"{key} sensor missing from DEVICE_CONTEXT_SENSORS"
    desc = matches[0].description
    assert MieleAppliance.DISHWASHER in matches[0].types
    return desc


def test_lifetime_sensor_defs() -> None:
    energy = _dw_sensor("lifetime_energy")
    water = _dw_sensor("lifetime_water")
    assert energy.native_unit_of_measurement == "Wh"
    assert energy.suggested_unit_of_measurement == "kWh"
    assert energy.suggested_display_precision == 1
    assert energy.device_class == "energy"
    assert energy.state_class == "total_increasing"
    assert water.native_unit_of_measurement == "L"
    assert water.device_class == "water"
    assert water.state_class == "total_increasing"


def test_lifetime_value_fns() -> None:
    energy = _dw_sensor("lifetime_energy")
    water = _dw_sensor("lifetime_water")
    ctx = _ctx(bytes.fromhex(F1.read_text().replace("\n", "")))
    assert energy.value_fn(ctx) == 182600
    assert water.value_fn(ctx) == 2447
    # Empty context (leaf absent / latched off) → None, not an error.
    assert energy.value_fn({}) is None
    assert water.value_fn({}) is None

# --------------------------------------------------------------- laundry
# WEJ885 WCS washer (EK057S, FW 09.14) and TEL785WP dryer (FW 04.05),
# both read on and idle on 2026-11-20. Their /State has no ExtendedState
# (404), so 2/1585 is the only local source of their totals.
#
# Washer: attr 5 = 114805 IntType 46, attr 6 = 9052 IntType 44 (attrs 3/4,
# 375 IntType 46 and 52 L, are the last cycle). The Miele app's last 12
# months (Nov 2025 - Oct 2026) were 102.4 kWh, 8129 L and 171 cycles:
# lifetime / 12 months is 1.121 for energy and 1.114 for water, so the two
# counters move together and IntType 46 is Wh, not kWh.
#
# Dryer: attr 5 = 59868 with IntType 0 (untagged). It fits Wh (app: 51.4
# kWh over 12 months) but nothing in the payload says so, so it stays
# unread.
W1 = Path(__file__).parent / "fixtures" / "wej885_dop2_1585.hex"
D1 = Path(__file__).parent / "fixtures" / "tel785wp_dop2_1585.hex"


def test_wej885_washer_lifetime_totals() -> None:
    ctx = _ctx(bytes.fromhex(W1.read_text().strip()))
    assert ctx["lifetime_energy_wh"] == 114805
    assert ctx["lifetime_water_l"] == 9052


def test_tel785wp_dryer_untagged_energy_is_not_read() -> None:
    ctx = _ctx(bytes.fromhex(D1.read_text().strip()))
    assert ctx["lifetime_energy_wh"] is None
    assert ctx["lifetime_water_l"] is None


def test_lifetime_sensors_cover_washers_but_not_dryers() -> None:
    for key in ("lifetime_energy", "lifetime_water"):
        [d] = [d for d in DEVICE_CONTEXT_SENSORS if d.description.key == key]
        assert MieleAppliance.WASHING_MACHINE in d.types
        assert MieleAppliance.TUMBLE_DRYER not in d.types
        assert MieleAppliance.WASHER_DRYER not in d.types
