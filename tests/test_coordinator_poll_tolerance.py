"""One transient poll failure must not flap every entity to unavailable."""
import logging
from types import SimpleNamespace

import pytest
from asyncmiele.exceptions.network import NetworkConnectionError, NetworkTimeoutError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.miele_lan.coordinator import (
    POLL_FAILURE_TOLERANCE,
    MieleLanCoordinator,
    MieleLanData,
    UpdateFailedUnreachable,
)


class _ScriptedClient:
    """get_state() replays `outcomes`: dicts are polls, exceptions raise."""

    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)

    async def get_state(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(raw_state=outcome)


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    # DataUpdateCoordinator reads HA's config-entry ContextVar.
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(outcomes: list):
        hass = HomeAssistant(str(tmp_path))
        coord = MieleLanCoordinator(
            hass,
            SimpleNamespace(entry_id="test"),
            _ScriptedClient(outcomes),
            "test-fridge",
        )
        # Pre-load ident so polls touch only get_state().
        coord._data = MieleLanData(state={"status": 5})
        coord._ident_loaded = True
        return coord

    return make


@pytest.mark.asyncio
async def test_single_failure_after_success_returns_last_known_data(make_coordinator):
    coord = make_coordinator([{"status": 5}, NetworkTimeoutError("xkm dropped it")])
    first = await coord._async_update_data()
    assert coord._poll_ever_succeeded is True

    result = await coord._async_update_data()

    assert result is first
    assert coord._consecutive_poll_failures == 1


@pytest.mark.asyncio
async def test_failures_beyond_tolerance_raise_update_failed(make_coordinator):
    coord = make_coordinator(
        [{"status": 5}]
        + [NetworkTimeoutError(str(i)) for i in range(POLL_FAILURE_TOLERANCE + 1)]
    )
    await coord._async_update_data()
    for _ in range(POLL_FAILURE_TOLERANCE):
        await coord._async_update_data()

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    assert coord._consecutive_poll_failures == POLL_FAILURE_TOLERANCE + 1


@pytest.mark.asyncio
async def test_success_between_failures_resets_counter(make_coordinator):
    coord = make_coordinator(
        [
            {"status": 5},
            NetworkTimeoutError("1"),
            {"status": 6},
            NetworkTimeoutError("2"),
            NetworkTimeoutError("3"),
        ]
    )
    await coord._async_update_data()
    await coord._async_update_data()
    await coord._async_update_data()
    assert coord._consecutive_poll_failures == 0

    await coord._async_update_data()
    await coord._async_update_data()
    assert coord._consecutive_poll_failures == POLL_FAILURE_TOLERANCE


@pytest.mark.asyncio
async def test_first_ever_failure_raises_immediately(make_coordinator):
    coord = make_coordinator([NetworkTimeoutError("never answered")])
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()
    assert coord._poll_ever_succeeded is False


@pytest.mark.asyncio
async def test_tolerated_failures_log_at_debug_only(make_coordinator, caplog):
    """HA's log-when-unavailable rule: nothing above debug while tolerated."""
    coord = make_coordinator(
        [{"status": 5}]
        + [NetworkTimeoutError(str(i)) for i in range(POLL_FAILURE_TOLERANCE)]
    )
    await coord._async_update_data()

    with caplog.at_level(logging.DEBUG, logger="custom_components.miele_lan"):
        for _ in range(POLL_FAILURE_TOLERANCE):
            await coord._async_update_data()

    tolerated = [r for r in caplog.records if "tolerated" in r.getMessage()]
    assert len(tolerated) == POLL_FAILURE_TOLERANCE
    assert all(r.levelno == logging.DEBUG for r in tolerated)


@pytest.mark.asyncio
async def test_unreachable_appliance_raises_the_unreachable_kind(make_coordinator):
    coord = make_coordinator(
        [NetworkConnectionError("Cannot connect to host 192.0.2.10:80")]
    )

    with pytest.raises(UpdateFailedUnreachable):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_other_failures_stay_plain_update_failed(make_coordinator):
    coord = make_coordinator([ValueError("garbled /State body")])

    with pytest.raises(UpdateFailed) as excinfo:
        await coord._async_update_data()
    if UpdateFailedUnreachable is not UpdateFailed:
        assert not isinstance(excinfo.value, UpdateFailedUnreachable)
