"""Light brightness and the second booster level of a hood on the old protocol (#88).

The samples are `/State.ExtendedState` reads of a DAS 4630 with the light
set from its own button (three steps) and from the Miele app. The layout is
the Miele app's: dim value at bytes 8-9, feature words at 34-35 and 37-38.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode

from custom_components.miele_lan.api import build_dop1_main_light_request
from custom_components.miele_lan.extended_state import (
    dop1_hood_has_booster_2,
    dop1_hood_light_dimmable,
    parse_dop1_hood_light_dim_value,
)
from custom_components.miele_lan.fan import MieleLanFan
from custom_components.miele_lan.light import MieleLanLight

BUTTON_1 = "001201010400000055550000000000000032002900B4002900000000000009000100C0131001C0000000000000000000000000000000000000000000000009000000"
BUTTON_2 = "0012010103000000AAAA0000000000000032002900B4002900000000000009000300C0131001C0000000000000000000000000000000000000000000000009000000"
BUTTON_3 = "0012010103000000FFFF0000000000000032002900B4002900000000000009000300C0131001C0000000000000000000000000000000000000000000000009000000"
APP_MINIMUM = "00120101040000000C0C0000000000000032002900B4002900000000000009000100C0131001C0000000000000000000000000000000000000000000000009000000"


def _with_u16(hex_str: str, offset: int, value: int) -> str:
    payload = bytearray(bytes.fromhex(hex_str))
    payload[offset:offset + 2] = value.to_bytes(2, "big")
    return payload.hex()


NOT_DIMMABLE = _with_u16(BUTTON_3, 34, 0xC003)
NO_BOOSTER_2 = _with_u16(BUTTON_3, 37, 0x0140)


# --- parser -----------------------------------------------------------------

def test_samples_are_66_bytes() -> None:
    assert len(bytes.fromhex(BUTTON_1)) == 66


@pytest.mark.parametrize(
    ("sample", "dim_value"),
    [(BUTTON_1, 0x5555), (BUTTON_2, 0xAAAA), (BUTTON_3, 0xFFFF), (APP_MINIMUM, 0x0C0C)],
)
def test_dim_value_follows_the_light(sample, dim_value) -> None:
    assert parse_dop1_hood_light_dim_value(sample) == dim_value


def test_feature_bits_of_the_das4630() -> None:
    assert dop1_hood_light_dimmable(BUTTON_1)
    assert dop1_hood_has_booster_2(BUTTON_1)


def test_feature_bits_can_be_absent() -> None:
    assert not dop1_hood_light_dimmable(NOT_DIMMABLE)
    assert dop1_hood_has_booster_2(NOT_DIMMABLE)
    assert dop1_hood_light_dimmable(NO_BOOSTER_2)
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
    assert parse_dop1_hood_light_dim_value(blob) is None
    assert not dop1_hood_light_dimmable(blob)
    assert not dop1_hood_has_booster_2(blob)


# --- light write ------------------------------------------------------------

def test_main_light_write_carries_the_dim_value() -> None:
    assert build_dop1_main_light_request(True, 0x5555) == (
        "14000200" + "0201040000000000005555" + "0000"
    )


def test_main_light_off_ignores_the_dim_value() -> None:
    assert build_dop1_main_light_request(False, 0x5555) == build_dop1_main_light_request(False)


@pytest.mark.parametrize("bad", [0, -1, 0x10000])
def test_main_light_rejects_a_dim_value_out_of_range(bad) -> None:
    with pytest.raises(ValueError):
        build_dop1_main_light_request(True, bad)


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


@pytest.mark.parametrize(
    ("sample", "brightness"),
    [(BUTTON_1, 85), (BUTTON_2, 170), (BUTTON_3, 255), (APP_MINIMUM, 12)],
)
def test_dimmable_hood_light_reports_brightness(sample, brightness) -> None:
    light = MieleLanLight(_coordinator(sample))
    assert light.color_mode is ColorMode.BRIGHTNESS
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert light.brightness == brightness


@pytest.mark.parametrize(
    "coord",
    [
        _coordinator(NOT_DIMMABLE),
        _coordinator(None),
        _coordinator(BUTTON_1, hood_dop1=False),
    ],
)
def test_other_lights_stay_on_off(coord) -> None:
    light = MieleLanLight(coord)
    assert light.color_mode is ColorMode.ONOFF
    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.brightness is None


def test_turn_on_with_brightness_writes_the_dim_value() -> None:
    coord = _coordinator(BUTTON_3)
    asyncio.run(MieleLanLight(coord).async_turn_on(**{ATTR_BRIGHTNESS: 85}))
    coord.client.set_main_light_dop1.assert_awaited_once_with(True, 0x5555)


def test_turn_on_without_brightness_restores_the_last_one(monkeypatch) -> None:
    monkeypatch.setattr(MieleLanLight, "async_write_ha_state", lambda self: None)
    coord = _coordinator(BUTTON_1)
    light = MieleLanLight(coord)
    light._handle_coordinator_update()
    coord.data.state.update({"Light": 2, "ExtendedState": _with_u16(BUTTON_1, 8, 0)})
    light._handle_coordinator_update()

    asyncio.run(light.async_turn_on())

    coord.client.set_main_light_dop1.assert_awaited_once_with(True, 0x5555)


def test_turn_on_without_a_known_brightness_is_full() -> None:
    coord = _coordinator(None)
    asyncio.run(MieleLanLight(coord).async_turn_on())
    coord.client.set_main_light_dop1.assert_awaited_once_with(True, 0xFFFF)
