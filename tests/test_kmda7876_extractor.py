"""KMDA7876 extractor captures, capability selection and coordinator updates."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan import fan, sensor
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData
from custom_components.miele_lan.extended_state import parse_kmda7876_extractor_speed

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures/kmda7876_extractor.json").read_text()
)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["label"])
def test_captured_speeds(sample):
    assert parse_kmda7876_extractor_speed(sample["ExtendedState"]) == sample["expected"]


@pytest.mark.parametrize("value", [None, 0, "", "xx", "00" * 62, "00" * 65, "00" * 67])
def test_invalid_or_different_layout_is_unknown(value):
    assert parse_kmda7876_extractor_speed(value) is None


def test_unobserved_speed_is_not_misreported_as_off():
    payload = bytearray.fromhex(SAMPLES[1]["ExtendedState"])
    payload[62] = 255
    assert parse_kmda7876_extractor_speed(payload.hex()) is None


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    # Production coordinator currently relies on HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(model="KMDA7876", device_type=MieleAppliance.HOB_INDUCTION):
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass, SimpleNamespace(entry_id="test"),
            SimpleNamespace(route="test-hob", host="192.0.2.1"), "test-hob",
        )
        coord._data = MieleLanData(
            ident={"tech_type": model, "device_type": device_type},
            state={"Status": 1, "ExtendedState": SAMPLES[0]["ExtendedState"]},
        )
        coord.data = coord._data
        hass.data[DOMAIN] = {"test": {"coordinators": {"hob": coord}}}
        return hass, coord

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize("model,device_type,expected", [
    ("KMDA7876", MieleAppliance.HOB_INDUCTION, True),
    ("kmda 7876", MieleAppliance.HOB_INDUCT_EXTR, True),
    ("KM8684", MieleAppliance.HOB_INDUCTION, False),
    ("KMDA9999", MieleAppliance.HOB_INDUCT_EXTR, False),
    ("", MieleAppliance.HOB_INDUCTION, False),
    ("KMDA7876", MieleAppliance.HOOD, False),
])
@pytest.mark.parametrize("platform,key", [(sensor, "extractor_speed"), (fan, "extractor")])
async def test_real_entity_selection(make_coordinator, model, device_type, expected, platform, key):
    hass, coord = make_coordinator(model, device_type)
    entities = []
    await platform.async_setup_entry(hass, SimpleNamespace(entry_id="test"), entities.extend)
    extractors = [e for e in entities if e.unique_id == f"test-hob_{key}"]
    assert len(extractors) == int(expected)
    if expected:
        entity = extractors[0]
        assert entity.unique_id == f"test-hob_{key}"
        assert entity.device_info["identifiers"] == {(DOMAIN, "test-hob")}
        if platform is sensor:
            assert entity.native_value == "off"
        else:
            assert entity.is_on is False
            assert entity.percentage == 0
            assert entity.speed_count == 4
            assert entity.supported_features == 0
        assert entity.should_poll is False


@pytest.mark.asyncio
async def test_entity_updates_and_listener_cleanup(make_coordinator, monkeypatch):
    hass, coord = make_coordinator()
    definition = next(d.description for d in sensor.SENSOR_TYPES if d.description.key == "extractor_speed")
    entity = sensor.MieleLanSensor(coord, definition)
    entity.hass = hass
    write_state = Mock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)
    await entity.async_added_to_hass()
    for sample in SAMPLES:
        coord.async_set_updated_data(MieleLanData(
            ident=coord.data.ident,
            state={"Status": 1, "ExtendedState": sample["ExtendedState"]},
        ))
        assert entity.native_value == sample["expected"]
        assert entity.available
    assert write_state.call_count == len(SAMPLES)
    coord.async_set_update_error(RuntimeError("offline"))
    assert not entity.available
    coord.async_set_updated_data(MieleLanData(state={"ExtendedState": "bad"}))
    assert entity.available
    assert entity.native_value is None
    await entity.async_will_remove_from_hass()
    assert list(coord.async_contexts()) == []


def test_translations_cover_sensor_options():
    root = Path(__file__).parents[1] / "custom_components/miele_lan"
    definition = next(d.description for d in sensor.SENSOR_TYPES if d.description.key == "extractor_speed")
    for name in ("strings.json", "translations/en.json"):
        states = json.loads((root / name).read_text())["entity"]["sensor"]["extractor_speed"]["state"]
        assert set(states) == set(definition.options)


@pytest.mark.asyncio
async def test_fan_updates_and_listener_cleanup(make_coordinator, monkeypatch):
    hass, coord = make_coordinator()
    entity = fan.MieleLanExtractorFan(coord)
    entity.hass = hass
    write_state = Mock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)
    await entity.async_added_to_hass()
    percentages = {"off": 0, "1": 25, "2": 50, "3": 75, "boost": 100}
    for sample in SAMPLES:
        coord.async_set_updated_data(MieleLanData(
            ident=coord.data.ident,
            state={"Status": 1, "ExtendedState": sample["ExtendedState"]},
        ))
        expected = percentages[sample["expected"]]
        assert entity.percentage == expected
        assert entity.is_on == (expected > 0)
        assert entity.extra_state_attributes == {"percentage": expected, "percentage_step": 25}
        assert entity.supported_features == 0
        assert entity.available
    assert write_state.call_count == len(SAMPLES)
    coord.async_set_update_error(RuntimeError("offline"))
    assert not entity.available
    unknown_speed = bytearray(66)
    unknown_speed[62] = 255
    for payload in (None, "bad", "00" * 65, unknown_speed.hex()):
        coord.async_set_updated_data(MieleLanData(state={"ExtendedState": payload}))
        assert entity.available
        assert entity.is_on is None
        assert entity.percentage is None
    await entity.async_will_remove_from_hass()
    assert list(coord.async_contexts()) == []


@pytest.mark.asyncio
async def test_hood_keeps_existing_controllable_fan(make_coordinator):
    hass, coord = make_coordinator("DA", MieleAppliance.HOOD)
    coord._data.ident["protocol_version"] = 2
    coord.data.state["VentilationStep"] = 2
    entities = []
    await fan.async_setup_entry(hass, SimpleNamespace(entry_id="test"), entities.extend)
    assert len(entities) == 1
    assert isinstance(entities[0], fan.MieleLanFan)
    assert entities[0].preset_mode == "2"
    assert entities[0].supported_features != 0
