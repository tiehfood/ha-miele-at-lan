"""Push status sensor states must be translatable.

coordinator.push_mode returns "push:active" and similar; ":" is not allowed in
a state translation key, so the sensor reports them with "_" instead.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.miele_lan.sensor import MieleLanPushStateSensor

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "miele_lan"
MODES = ["push:active", "push:ready", "subs:ready", "polling"]


def _sensor(mode: str) -> MieleLanPushStateSensor:
    coord = SimpleNamespace(client=SimpleNamespace(route="000000000000"), push_mode=mode)
    return MieleLanPushStateSensor(coord)


@pytest.mark.parametrize("mode", MODES)
def test_every_push_mode_is_an_option(mode: str) -> None:
    sensor = _sensor(mode)
    assert sensor.native_value in sensor.options


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
def test_every_option_is_translated(path: str) -> None:
    states = json.loads((BASE / path).read_text())["entity"]["sensor"]["push_mode"]["state"]
    assert set(states) == set(_sensor("polling").options)
