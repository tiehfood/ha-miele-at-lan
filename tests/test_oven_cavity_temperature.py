"""H6800BMX cavity temperature from `/State.ExtendedState` bytes 45-46.

The captures in fixtures/h6800bmx_oven.json come from a H6800BMX (XKM3100W,
firmware 02.72) heating to 30 C and then to 100 C. The values matched the
Miele app on the way up and at the 100 C target, and /State.Temperature
stayed at the -32768 sentinel the whole time.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.miele_lan.extended_state import parse_h6800bmx_cavity_temperature
from custom_components.miele_lan.sensor import SENSOR_TYPES, _oven_cavity_temperature

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/h6800bmx_oven.json").read_text()
)
SENTINEL_TEMPERATURE = [-32768, -32768, -32768]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["label"])
def test_captured_temperatures(sample):
    assert parse_h6800bmx_cavity_temperature(sample["ExtendedState"]) == sample["expected"]


@pytest.mark.parametrize("value", [None, 0, "", "xx", "00" * 16, "00" * 48, "00" * 50])
def test_invalid_or_different_layout_is_unknown(value):
    assert parse_h6800bmx_cavity_temperature(value) is None


def test_temperature_is_a_signed_big_endian_s16_in_hundredths():
    payload = bytearray(49)
    payload[45:47] = (23456).to_bytes(2, "big", signed=True)
    assert parse_h6800bmx_cavity_temperature(payload.hex()) == 234.56
    payload[45:47] = (-500).to_bytes(2, "big", signed=True)
    assert parse_h6800bmx_cavity_temperature(payload.hex()) == -5.0


def _data(model, extended, temperature=None):
    return SimpleNamespace(
        ident={"tech_type": model},
        state={"Temperature": temperature or SENTINEL_TEMPERATURE, "ExtendedState": extended},
    )


def test_h6800bmx_falls_back_to_the_extended_state():
    assert _oven_cavity_temperature(_data("H6800BMX", SAMPLES[3]["ExtendedState"])) == 100.0
    assert _oven_cavity_temperature(_data("h6800 bmx", SAMPLES[3]["ExtendedState"])) == 100.0


def test_h6800bmx_off_is_unknown():
    assert _oven_cavity_temperature(_data("H6800BMX", SAMPLES[-1]["ExtendedState"])) is None


@pytest.mark.parametrize("model", ["H7560BP", "", "H6800BM", "H6800BMX2"])
def test_other_models_do_not_use_the_extended_state(model):
    assert _oven_cavity_temperature(_data(model, SAMPLES[3]["ExtendedState"])) is None


def test_state_temperature_still_wins_when_present():
    assert _oven_cavity_temperature(
        _data("H6800BMX", SAMPLES[3]["ExtendedState"], temperature=[18000, -32768, -32768])
    ) == 180.0
    assert _oven_cavity_temperature(
        _data("H7560BP", None, temperature=[18000, -32768, -32768])
    ) == 180.0


def test_cavity_temperature_sensor_reads_through_the_model_aware_function():
    d = [x.description for x in SENSOR_TYPES if x.description.key == "cavity_temperature"]
    assert len(d) == 1
    assert d[0].data_value_fn is not None
