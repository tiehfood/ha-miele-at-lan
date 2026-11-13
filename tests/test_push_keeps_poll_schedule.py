"""A push must not push back the next poll.

DataUpdateCoordinator.async_set_updated_data re-arms the refresh timer, so
merging every push through it meant the poll (and everything only it reads:
the dishwasher door from 2/1585 (#59), device context, WLAN, hours) never ran
while pushes kept arriving faster than the poll interval.
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator
from custom_components.miele_lan.push_listener import PushEvent

FAB = "000000000001"


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)
    return lambda: _coordinator(tmp_path)


def _coordinator(tmp_path) -> MieleLanCoordinator:
    client = SimpleNamespace(raw=None, wake=AsyncMock(return_value={}))
    coord = MieleLanCoordinator(
        HomeAssistant(str(tmp_path)), SimpleNamespace(entry_id="test"), client, FAB
    )
    coord._data.ident = {"device_type": int(MieleAppliance.DISHWASHER)}
    coord._data.state = {"Status": 5}
    coord.data = coord._data
    coord.update_interval = timedelta(seconds=60)
    return coord


def _push(content: dict) -> PushEvent:
    return PushEvent(
        peer_fab=FAB, resource=f"/Devices/{FAB}/State/", content=content,
        host_name=None, raw_plain="",
    )


@pytest.mark.asyncio
async def test_push_leaves_the_poll_timer_alone(make_coordinator) -> None:
    coord = make_coordinator()
    unsub = coord.async_add_listener(lambda: None)
    coord._schedule_refresh()
    timer = coord._unsub_refresh

    coord.apply_push(_push({"RemainingTime": [0, 42]}))

    assert coord._unsub_refresh is timer
    unsub()
    coord._async_unsub_refresh()


@pytest.mark.asyncio
async def test_push_still_updates_entities(make_coordinator) -> None:
    coord = make_coordinator()
    calls = []
    unsub = coord.async_add_listener(lambda: calls.append(coord.data.state["RemainingTime"]))
    coord.last_update_success = False

    coord.apply_push(_push({"RemainingTime": [0, 42]}))

    assert calls == [[0, 42]]
    assert coord.last_update_success is True
    unsub()
    coord._async_unsub_refresh()
