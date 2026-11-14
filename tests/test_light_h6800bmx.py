"""The H6800BMX reports Light = 0 whatever the lamp does and refuses writes (403).

A light entity would stay unknown for ever, so it is not created. Other
models keep their light entity, including those that report 0 for the moment.
"""
from types import SimpleNamespace

import pytest

from custom_components.miele_lan.light import _light_is_dead


def _data(model, light):
    return SimpleNamespace(ident={"tech_type": model}, state={"Light": light})


@pytest.mark.parametrize("model", ["H6800BMX", "h6800 bmx"])
def test_h6800bmx_without_light_state_has_no_light(model):
    assert _light_is_dead(_data(model, 0))


def test_h6800bmx_with_light_state_keeps_the_light():
    assert not _light_is_dead(_data("H6800BMX", 1))
    assert not _light_is_dead(_data("H6800BMX", 2))


def test_other_models_keep_the_light_even_at_zero():
    assert not _light_is_dead(_data("DA6698", 0))
    assert not _light_is_dead(_data("", 0))
