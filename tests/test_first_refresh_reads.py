"""The first refresh reads only what setup needs; optional reads follow.

The first refresh runs inside setup, and setup waits for it. On a WWV980 and
a TWV680WP, DOP2 2/1585 times out after 10 s on every start, so setup took
10 s longer than it had to. The optional DOP2 and filter reads now start with
the next poll; their entities are added once their data arrives.
"""
from types import SimpleNamespace

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.miele_lan.const import MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator


class _Raw:
    def __init__(self) -> None:
        self.resources: list[str] = []

    async def _request_bytes(self, method: str, resource: str, **kwargs):
        self.resources.append(resource)
        return 404, b""


class _Client:
    def __init__(self) -> None:
        self.raw = _Raw()

    async def get_state(self):
        return SimpleNamespace(raw_state={"Status": 1})


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(device_type: MieleAppliance) -> MieleLanCoordinator:
        coord = MieleLanCoordinator(
            HomeAssistant(str(tmp_path)),
            SimpleNamespace(entry_id="test"),
            _Client(),
            "000000000001",
        )
        ident = {"device_type": int(device_type)}

        async def _ident():
            return ident

        async def _wlan():
            return {"rssi": -60}

        monkeypatch.setattr(coord, "_fetch_full_ident", _ident)
        monkeypatch.setattr(coord, "_fetch_wlan", _wlan)
        return coord

    return make


def _dop2_reads(coord: MieleLanCoordinator) -> list[str]:
    return [r.split("/DOP2/")[1].split("?")[0] for r in coord.client.raw.resources if "/DOP2/" in r]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_type", "optional_leaves"),
    [
        (MieleAppliance.WASHING_MACHINE, {"2/119", "2/1585"}),
        # 2/1584 (the programme list) only follows a 2/1585 that answered 200.
        (MieleAppliance.DISHWASHER, {"2/1585"}),
    ],
)
async def test_first_refresh_leaves_the_optional_reads_to_the_next_poll(
    make_coordinator, device_type, optional_leaves
) -> None:
    coord = make_coordinator(device_type)

    await coord._async_update_data()  # the first refresh, inside setup

    assert coord._data.state == {"Status": 1}
    assert _dop2_reads(coord) == []

    await coord._async_update_data()  # the next poll

    assert set(_dop2_reads(coord)) == optional_leaves


@pytest.mark.asyncio
async def test_a_failed_first_refresh_still_defers_the_optional_reads(
    make_coordinator,
) -> None:
    """Home Assistant retries a setup whose first refresh failed; the retry
    is still inside setup."""
    coord = make_coordinator(MieleAppliance.WASHING_MACHINE)

    async def _no_state():
        raise TimeoutError

    coord.client.get_state = _no_state
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    del coord.client.get_state  # the class's working get_state again
    await coord._async_update_data()

    assert _dop2_reads(coord) == []
