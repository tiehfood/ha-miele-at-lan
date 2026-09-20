"""MieleLanCoordinator.async_ensure_ident_ready — the bounded /Ident retry run
before platform setup forwards capability-gated entities (hood fan, hood
filters, extractor speed). See coordinator.py docstring for why this exists:
those entities are created once at forward time and won't reappear until a
reload if /Ident was still empty on the very first refresh.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan import fan
from custom_components.miele_lan.const import DOMAIN, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator, MieleLanData


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    # Production coordinator currently relies on HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)
    # Patches never actually wait — this test suite must not take real seconds.
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    def make(ident: dict | None = None):
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass, SimpleNamespace(entry_id="test"),
            SimpleNamespace(route="test-hood", host="192.0.2.1"), "test-hood",
        )
        coord._data = MieleLanData(
            ident=ident if ident is not None else {},
            state={"VentilationStep": 2},
        )
        coord.data = coord._data
        hass.data[DOMAIN] = {"test": {"coordinators": {"hood": coord}}}
        return hass, coord

    return make


@pytest.mark.asyncio
async def test_ident_present_first_try_does_no_extra_work(make_coordinator):
    hass, coord = make_coordinator({"device_type": MieleAppliance.HOOD, "protocol_version": 2})
    coord._fetch_full_ident = AsyncMock(side_effect=AssertionError("should not be called"))

    await coord.async_ensure_ident_ready()

    coord._fetch_full_ident.assert_not_called()
    entities: list = []
    await fan.async_setup_entry(hass, SimpleNamespace(entry_id="test"), entities.extend)
    assert len(entities) == 1
    assert isinstance(entities[0], fan.MieleLanFan)


@pytest.mark.asyncio
async def test_ident_empty_then_populated_creates_gated_entities(make_coordinator, caplog):
    hass, coord = make_coordinator({})
    populated = {"device_type": MieleAppliance.HOOD, "protocol_version": 2}
    coord._fetch_full_ident = AsyncMock(side_effect=[{}, populated])

    with caplog.at_level("INFO"):
        await coord.async_ensure_ident_ready(attempts=3, delay=0)

    assert coord._fetch_full_ident.call_count == 2
    assert coord._data.ident == populated
    assert coord._ident_loaded is True
    assert any("setup retry" in r.message for r in caplog.records)

    entities: list = []
    await fan.async_setup_entry(hass, SimpleNamespace(entry_id="test"), entities.extend)
    assert len(entities) == 1
    assert isinstance(entities[0], fan.MieleLanFan)


@pytest.mark.asyncio
async def test_ident_never_populated_setup_completes_without_looping(make_coordinator, caplog):
    hass, coord = make_coordinator({})
    coord._fetch_full_ident = AsyncMock(return_value={})

    with caplog.at_level("DEBUG"):
        await coord.async_ensure_ident_ready(attempts=3, delay=0)

    assert coord._fetch_full_ident.call_count == 3
    assert coord._data.ident == {}
    assert coord._ident_loaded is False
    assert any("still empty after 3 setup retries" in r.message for r in caplog.records)

    entities: list = []
    await fan.async_setup_entry(hass, SimpleNamespace(entry_id="test"), entities.extend)
    assert entities == []


@pytest.mark.asyncio
async def test_poll_budget_survives_a_never_populated_setup_burst(make_coordinator):
    hass, coord = make_coordinator({})
    # async_config_entry_first_refresh already spent attempt 1 of the
    # poll-time budget before async_ensure_ident_ready ever runs.
    coord._ident_attempts = 1

    # Setup burst: ident never populates across all 3 setup retries.
    coord._fetch_full_ident = AsyncMock(return_value={})
    await coord.async_ensure_ident_ready(attempts=3, delay=0)

    assert coord._ident_attempts == 1, "setup burst must not spend the poll-time budget"
    assert coord._ident_setup_attempts == 3
    assert coord._ident_loaded is False

    # A later poll finally gets a populated ident — it must still land: the
    # poll-time budget was untouched by the setup burst, so this is only the
    # second poll-time attempt, nowhere near IDENT_MAX_ATTEMPTS.
    populated = {"device_type": MieleAppliance.HOOD, "protocol_version": 2}
    coord.client.get_state = AsyncMock(return_value=SimpleNamespace(raw_state={}))
    coord._fetch_wlan = AsyncMock(return_value={})
    coord.client.read_setting_pf = AsyncMock(return_value=None)
    coord._fetch_full_ident = AsyncMock(return_value=populated)

    await coord._async_update_data()

    assert coord._ident_attempts == 2
    assert coord._data.ident == populated
    assert coord._ident_loaded is True
