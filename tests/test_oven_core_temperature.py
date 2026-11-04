"""H6800BMX food probe temperature from `/State.ExtendedState` bytes 47-48.

The captures in fixtures/h6800bmx_oven_probe.json come from a H6800BMX
(XKM3100W, firmware 02.72) on the Auto roasting programme. The probe followed
the oven display at 21, 22, 23, 24, 26 and 27 C, the 26 C capture is stored
here. With no probe plugged in the bytes hold the s16 sentinel -32768. The
probe target (40 and 60 C were tried) is not in the blob.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.miele_lan.extended_state import (
    parse_h6800bmx_cavity_temperature,
    parse_h6800bmx_core_temperature,
)
from custom_components.miele_lan.sensor import SENSOR_TYPES, _oven_core_temperature

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/h6800bmx_oven_probe.json").read_text()
)
SENTINEL = [-32768, -32768, -32768]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["label"])
def test_captured_probe_and_cavity(sample):
    assert parse_h6800bmx_core_temperature(sample["ExtendedState"]) == sample["probe"]
    assert parse_h6800bmx_cavity_temperature(sample["ExtendedState"]) == sample["cavity"]


@pytest.mark.parametrize("probe", [21, 22, 23, 24, 26, 27])
def test_probe_follows_whole_degrees_on_the_display(probe):
    payload = bytearray(bytes.fromhex(SAMPLES[1]["ExtendedState"]))
    payload[47:49] = (probe * 100).to_bytes(2, "big", signed=True)
    assert parse_h6800bmx_core_temperature(payload.hex()) == float(probe)


@pytest.mark.parametrize("value", [None, 0, "", "xx", "00" * 16, "00" * 48, "00" * 50])
def test_invalid_or_different_layout_is_unknown(value):
    assert parse_h6800bmx_core_temperature(value) is None


def _data(model, extended, temperature=None):
    return SimpleNamespace(
        ident={"tech_type": model},
        state={"Temperature": temperature or SENTINEL, "ExtendedState": extended},
    )


def test_h6800bmx_falls_back_to_the_probe_bytes():
    assert _oven_core_temperature(_data("H6800BMX", SAMPLES[1]["ExtendedState"])) == 26.0
    assert _oven_core_temperature(_data("h6800 bmx", SAMPLES[1]["ExtendedState"])) == 26.0


def test_no_probe_is_unknown():
    assert _oven_core_temperature(_data("H6800BMX", SAMPLES[0]["ExtendedState"])) is None


@pytest.mark.parametrize("model", ["H7560BP", "", "H6800BM", "H6800BMX2"])
def test_other_models_do_not_use_the_extended_state(model):
    assert _oven_core_temperature(_data(model, SAMPLES[1]["ExtendedState"])) is None


def test_state_temperature_still_wins_when_present():
    assert _oven_core_temperature(
        _data("H6800BMX", SAMPLES[1]["ExtendedState"], temperature=[18000, 5500, -32768])
    ) == 55.0


def test_core_temperature_sensor_reads_through_the_model_aware_function():
    d = [x.description for x in SENSOR_TYPES if x.description.key == "core_temperature"]
    assert len(d) == 1 and d[0].data_value_fn is not None
