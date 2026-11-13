"""Entities fed by slow reads appear once their data does, not only at setup.

Since washers and dryers are no longer woken for DOP2 2/1585 (#65, #69), one
asleep at HA start has no device context at platform setup. Its TwinDos and
Wash2Dry entities must still appear when the leaf first answers.
"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan import binary_sensor as binary_sensor_mod
from custom_components.miele_lan import sensor as sensor_mod
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator
from custom_components.miele_lan.dop2 import parse_global_device_context

FAB = "000000000001"
WTW870 = bytes.fromhex((Path(__file__).parent / "fixtures" / "wtw870_dop2_1585.hex").read_text().strip())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    async def run(device_context: dict | None = None):
        client = SimpleNamespace(raw=None, wake=AsyncMock(return_value={}), route=FAB)
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(hass, SimpleNamespace(entry_id="e"), client, FAB)
        coord._data.ident = {"device_type": int(MieleAppliance.WASHING_MACHINE)}
        coord._data.state = {"Status": 1}
        coord._data.device_context = device_context or {}
        coord.data = coord._data
        hass.data[DOMAIN] = {"e": {"coordinators": {FAB: coord}}}
        unloads = []
        entry = SimpleNamespace(entry_id="e", async_on_unload=unloads.append)
        added = []
        await sensor_mod.async_setup_entry(hass, entry, added.extend)
        await binary_sensor_mod.async_setup_entry(hass, entry, added.extend)
        return coord, added, unloads

    return run


def _device_context_keys(entities) -> list[str]:
    return sorted(
        e.entity_description.key for e in entities
        if isinstance(e, (sensor_mod.MieleLanDeviceContextSensor,
                          binary_sensor_mod.MieleLanDeviceContextBinarySensor))
    )


@pytest.mark.asyncio
async def test_device_context_entities_follow_the_first_answer(setup) -> None:
    coord, added, unloads = await setup()
    assert _device_context_keys(added) == []

    coord._data.device_context = parse_global_device_context(WTW870)
    coord.async_update_listeners()
    keys = _device_context_keys(added)
    assert keys
    assert any("twindos" in k for k in keys)

    coord.async_update_listeners()
    assert _device_context_keys(added) == keys  # no duplicates

    for unsub in unloads:
        unsub()
    coord._async_unsub_refresh()


@pytest.mark.asyncio
async def test_present_at_setup_is_added_once(setup) -> None:
    coord, added, unloads = await setup(parse_global_device_context(WTW870))
    first = _device_context_keys(added)
    assert first
    coord.async_update_listeners()
    assert _device_context_keys(added) == first
    for unsub in unloads:
        unsub()
    coord._async_unsub_refresh()
