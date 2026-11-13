"""WMV960 washer energy and water counters decoded from `/State.ExtendedState`.

The two captures in fixtures/wmv960_washer.json come from a WMV960 WPS
(XKM3100W, firmware 02.72) running a 60 C cotton cycle. Water was compared
with the Miele app while it rose from 23 to 68 litres and energy went from
1.4 to 1.5 kWh.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan import sensor
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData
from custom_components.miele_lan.extended_state import (
    parse_wmv960_energy_kwh,
    parse_wmv960_water_litres,
)

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/wmv960_washer.json").read_text()
)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["label"])
def test_captured_counters(sample):
    assert parse_wmv960_energy_kwh(sample["ExtendedState"]) == sample["energy_kwh"]
    assert parse_wmv960_water_litres(sample["ExtendedState"]) == sample["water_litres"]


@pytest.mark.parametrize("value", [None, 0, "", "xx", "00" * 16, "00" * 66, "00" * 68])
def test_invalid_or_different_layout_is_unknown(value):
    assert parse_wmv960_energy_kwh(value) is None
    assert parse_wmv960_water_litres(value) is None


def test_counters_are_big_endian_u16():
    payload = bytearray(67)
    payload[50:52] = (1234).to_bytes(2, "big")
    payload[52:54] = (321).to_bytes(2, "big")
    assert parse_wmv960_energy_kwh(payload.hex()) == 123.4
    assert parse_wmv960_water_litres(payload.hex()) == 321


def test_sensor_descriptions_are_total_increasing_with_energy_and_water_classes():
    by_key = {d.description.key: d.description for d in sensor.SENSOR_TYPES}
    energy = by_key["energy_consumption"]
    water = by_key["water_consumption"]
    assert energy.device_class is SensorDeviceClass.ENERGY
    assert energy.native_unit_of_measurement == "kWh"
    assert energy.state_class is SensorStateClass.TOTAL_INCREASING
    assert water.device_class is SensorDeviceClass.WATER
    assert water.native_unit_of_measurement == "L"
    assert water.state_class is SensorStateClass.TOTAL_INCREASING


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    # Production coordinator currently relies on HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(model="WMV960", device_type=MieleAppliance.WASHING_MACHINE):
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass, SimpleNamespace(entry_id="test", async_on_unload=lambda unsub: None),
            SimpleNamespace(route="test-washer", host="192.0.2.1"), "test-washer",
        )
        coord._data = MieleLanData(
            ident={"tech_type": model, "device_type": device_type},
            state={"Status": 7, "ExtendedState": SAMPLES[1]["ExtendedState"]},
        )
        coord.data = coord._data
        hass.data[DOMAIN] = {"test": {"coordinators": {"washer": coord}}}
        return hass, coord

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("model,device_type,expected", [
    ("WMV960", MieleAppliance.WASHING_MACHINE, True),
    ("wmv 960", MieleAppliance.WASHING_MACHINE, True),
    ("WEC395", MieleAppliance.WASHING_MACHINE, False),
    ("WMV961", MieleAppliance.WASHING_MACHINE, False),
    ("", MieleAppliance.WASHING_MACHINE, False),
    ("WMV960", MieleAppliance.WASHER_DRYER, False),
    ("WMV960", MieleAppliance.TUMBLE_DRYER, False),
])
async def test_real_entity_selection(make_coordinator, model, device_type, expected):
    hass, coord = make_coordinator(model, device_type)
    entities = []
    await sensor.async_setup_entry(hass, SimpleNamespace(entry_id="test", async_on_unload=lambda unsub: None), entities.extend)
    by_id = {e.unique_id: e for e in entities}
    energy = by_id.get("test-washer_energy_consumption")
    water = by_id.get("test-washer_water_consumption")
    assert (energy is not None) == expected
    assert (water is not None) == expected
    if expected:
        assert energy.native_value == 1.5
        assert water.native_value == 68
        assert energy.device_info["identifiers"] == {(DOMAIN, "test-washer")}

