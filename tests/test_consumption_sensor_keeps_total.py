"""Energy and water totals survive the appliance being switched off.

A washer or dishwasher that is switched off reports an empty ExtendedState.
Without the memory the sensors turned unknown and the Energy dashboard
reported an error for them for most of the day.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan import sensor
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData

SAMPLES = json.loads((Path(__file__).parent / "fixtures/g6992_dishwasher.json").read_text())
RUNNING = SAMPLES[5]["ExtendedState"]  # 0.5 kWh and 3.8 l
ZERO = SAMPLES[0]["ExtendedState"]


@pytest.fixture
def make_entities(tmp_path, monkeypatch):
    # Production coordinator currently relies on HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make():
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass, SimpleNamespace(entry_id="test"),
            SimpleNamespace(route="test-dish", host="192.0.2.1"), "test-dish",
        )
        coord._data = MieleLanData(
            ident={"tech_type": "G6992", "device_type": MieleAppliance.DISHWASHER},
            state={"Status": 5, "ExtendedState": RUNNING},
        )
        coord.data = coord._data
        hass.data[DOMAIN] = {"test": {"coordinators": {"dish": coord}}}

        async def build():
            out = []
            await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="test"), out.extend)
            return {e.unique_id: e for e in out}

        return coord, build

    return make


@pytest.mark.asyncio
async def test_total_is_kept_when_the_blob_goes_empty(make_entities):
    coord, build = make_entities()
    by_id = await build()
    energy = by_id["test-dish_energy_consumption"]
    water = by_id["test-dish_water_consumption"]
    assert isinstance(energy, sensor.MieleLanConsumptionSensor)
    assert (energy.native_value, water.native_value) == (0.5, 3.8)

    coord._data.state["ExtendedState"] = ""  # switched off
    assert (energy.native_value, water.native_value) == (0.5, 3.8)


@pytest.mark.asyncio
async def test_new_cycle_reset_is_passed_through(make_entities):
    coord, build = make_entities()
    by_id = await build()
    energy = by_id["test-dish_energy_consumption"]
    assert energy.native_value == 0.5
    coord._data.state["ExtendedState"] = ZERO  # next cycle starts at zero
    assert energy.native_value == 0.0
    coord._data.state["ExtendedState"] = ""
    assert energy.native_value == 0.0


@pytest.mark.asyncio
async def test_restored_total_is_used_until_a_value_is_read(make_entities, monkeypatch):
    coord, build = make_entities()
    by_id = await build()
    water = by_id["test-dish_water_consumption"]

    async def _last():
        return SimpleNamespace(native_value=9.8)

    monkeypatch.setattr(water, "async_get_last_sensor_data", _last)
    # Skip the registry and state-machine plumbing, only the restore step matters.
    monkeypatch.setattr(
        sensor.MieleLanSensor, "async_added_to_hass", lambda self: _noop(), raising=False
    )
    coord._data.state["ExtendedState"] = ""
    await water.async_added_to_hass()
    assert water.native_value == 9.8


async def _noop():
    return None


@pytest.mark.asyncio
async def test_a_restored_non_number_is_ignored(make_entities, monkeypatch):
    coord, build = make_entities()
    by_id = await build()
    water = by_id["test-dish_water_consumption"]

    async def _last():
        return SimpleNamespace(native_value="unknown")

    monkeypatch.setattr(water, "async_get_last_sensor_data", _last)
    monkeypatch.setattr(
        sensor.MieleLanSensor, "async_added_to_hass", lambda self: _noop(), raising=False
    )
    coord._data.state["ExtendedState"] = ""
    await water.async_added_to_hass()
    assert water.native_value is None
