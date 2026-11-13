"""G6992 dishwasher energy and water counters decoded from `/State.ExtendedState`.

The captures in fixtures/g6992_dishwasher.json come from a G6992 (EK039W,
firmware 02.72) running a Quick PowerWash and an Automatic cycle. Water
matched the Miele app at 3.7, 3.8, 5.3 and 10.7 litres, energy at 0.4 and
0.5 kWh. The last sample is the programmed dishwasher before the first
cycle. Its 1.3 kWh and 9.8 l were not compared with the app, they are just
what the same layout decodes there and what the counters then reset from.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.miele_lan import sensor
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData
from custom_components.miele_lan.extended_state import (
    parse_g6992_energy_kwh,
    parse_g6992_water_litres,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/g6992_dishwasher.json").read_text()
)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["label"])
def test_captured_counters(sample):
    assert parse_g6992_energy_kwh(sample["ExtendedState"]) == sample["energy_kwh"]
    assert parse_g6992_water_litres(sample["ExtendedState"]) == sample["water_litres"]


@pytest.mark.parametrize("value", [None, 0, "", "xx", "00" * 16, "00" * 23, "00" * 25, "00" * 67])
def test_invalid_or_different_layout_is_unknown(value):
    assert parse_g6992_energy_kwh(value) is None
    assert parse_g6992_water_litres(value) is None


def test_counters_layout():
    payload = bytearray(24)
    payload[16] = 17
    payload[17:19] = (1234).to_bytes(2, "big")
    assert parse_g6992_energy_kwh(payload.hex()) == 1.7
    assert parse_g6992_water_litres(payload.hex()) == 123.4


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    # Production coordinator currently relies on HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(model="G6992", device_type=MieleAppliance.DISHWASHER, extended=None):
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass, SimpleNamespace(entry_id="test", async_on_unload=lambda unsub: None),
            SimpleNamespace(route="test-dish", host="192.0.2.1"), "test-dish",
        )
        coord._data = MieleLanData(
            ident={"tech_type": model, "device_type": device_type},
            state={"Status": 5, "ExtendedState": extended or SAMPLES[4]["ExtendedState"]},
        )
        coord.data = coord._data
        hass.data[DOMAIN] = {"test": {"coordinators": {"dish": coord}}}
        return hass, coord

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("model,device_type,expected", [
    ("G6992", MieleAppliance.DISHWASHER, True),
    ("g 6992", MieleAppliance.DISHWASHER, True),
    ("G7310", MieleAppliance.DISHWASHER, False),
    ("G6993", MieleAppliance.DISHWASHER, False),
    ("", MieleAppliance.DISHWASHER, False),
    ("G6992", MieleAppliance.WASHING_MACHINE, False),
    ("G6992", MieleAppliance.OVEN, False),
])
async def test_real_entity_selection(make_coordinator, model, device_type, expected):
    hass, coord = make_coordinator(model, device_type)
    entities = []
    await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="test", async_on_unload=lambda unsub: None), entities.extend)
    by_id = {e.unique_id: e for e in entities}
    energy = by_id.get("test-dish_energy_consumption")
    water = by_id.get("test-dish_water_consumption")
    assert (energy is not None) == expected
    assert (water is not None) == expected
    if expected:
        assert energy.native_value == 0.4
        assert water.native_value == 3.8
        assert energy.device_info["identifiers"] == {(DOMAIN, "test-dish")}


@pytest.mark.asyncio
async def test_washer_layout_is_not_used_for_a_dishwasher(make_coordinator):
    """A 67 byte washer blob on a G6992 must read as unknown, not as counters."""
    washer_blob = "00" * 67
    hass, coord = make_coordinator(extended=washer_blob)
    entities = []
    await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="test", async_on_unload=lambda unsub: None), entities.extend)
    by_id = {e.unique_id: e for e in entities}
    assert by_id["test-dish_energy_consumption"].native_value is None
    assert by_id["test-dish_water_consumption"].native_value is None
