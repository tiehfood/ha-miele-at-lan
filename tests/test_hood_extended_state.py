"""The second booster level of a hood on the old protocol (#88).

The sample is a `/State.ExtendedState` read of a DAS 4630. The layout is the
Miele app's: the second feature word sits at bytes 37-38.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.miele_lan.extended_state import dop1_hood_has_booster_2
from custom_components.miele_lan.fan import MieleLanFan

BUTTON_1 = "001201010400000055550000000000000032002900B4002900000000000009000100C0131001C0000000000000000000000000000000000000000000000009000000"


def _with_u16(hex_str: str, offset: int, value: int) -> str:
    payload = bytearray(bytes.fromhex(hex_str))
    payload[offset:offset + 2] = value.to_bytes(2, "big")
    return payload.hex()


NO_BOOSTER_2 = _with_u16(BUTTON_1, 37, 0x0140)


# --- parser -----------------------------------------------------------------

def test_das4630_reports_a_second_booster_level() -> None:
    assert len(bytes.fromhex(BUTTON_1)) == 66
    assert dop1_hood_has_booster_2(BUTTON_1)
    assert not dop1_hood_has_booster_2(NO_BOOSTER_2)


@pytest.mark.parametrize(
    "blob",
    [
        None,
        "",
        "zz",
        BUTTON_1[:60],
        "0001" + BUTTON_1[4:],  # a washing machine's header
    ],
)
def test_anything_else_is_not_read(blob) -> None:
    assert not dop1_hood_has_booster_2(blob)


# --- entities ---------------------------------------------------------------

def _coordinator(extended, *, hood_dop1=True, light=1, step=1):
    state = {"Light": light, "VentilationStep": step}
    if extended is not None:
        state["ExtendedState"] = extended
    return SimpleNamespace(
        data=SimpleNamespace(state=state),
        hood_dop1_supported=hood_dop1,
        client=SimpleNamespace(
            route="000000000001",
            set_main_light_dop1=AsyncMock(),
            set_fan_level=AsyncMock(),
            light_on=AsyncMock(),
        ),
        async_request_refresh=AsyncMock(),
    )


def test_fan_offers_boost_2_only_when_the_hood_reports_it() -> None:
    assert MieleLanFan(_coordinator(BUTTON_1)).preset_modes == ["1", "2", "3", "boost", "boost_2"]
    assert MieleLanFan(_coordinator(NO_BOOSTER_2)).preset_modes == ["1", "2", "3", "boost"]
    assert MieleLanFan(_coordinator(None)).preset_modes == ["1", "2", "3", "boost"]


def test_fan_reports_step_5_as_boost_2() -> None:
    assert MieleLanFan(_coordinator(BUTTON_1, step=5)).preset_mode == "boost_2"
    assert MieleLanFan(_coordinator(NO_BOOSTER_2, step=5)).preset_mode is None
    assert MieleLanFan(_coordinator(BUTTON_1, step=4)).preset_mode == "boost"


def test_fan_writes_level_5_for_boost_2() -> None:
    coord = _coordinator(BUTTON_1)
    asyncio.run(MieleLanFan(coord).async_set_preset_mode("boost_2"))
    coord.client.set_fan_level.assert_awaited_once_with(5)
