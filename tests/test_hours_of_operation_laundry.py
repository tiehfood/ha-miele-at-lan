"""Hours of operation (DOP2 2/119) on washers and dryers (issue #87).

The fixtures are live reads of a WCR890 washer and a TCR870WP dryer (both
XKM EK037, FW 04.05), taken before and after a programme. The deltas match
the programme runtime from the Home Assistant status history to within a few
minutes, which pins attr 1 as minutes on laundry appliances too:

- WCR890 load 1: 94469 -> 94671 (+202), ran 21:12:22 -> 00:33:52 (201.5 min)
- WCR890 load 2: 94671 -> 94867 (+196), ran 01:51:31 -> 05:06:47 (195.3 min)
- TCR870WP:      65860 -> 66020 (+160), ran 01:49:26 -> 04:34:07 (164.7 min)

The washer was switched on without a programme for several minutes before
and after, and the counter did not move, so it counts programme runtime.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from asyncmiele.exceptions.network import ResponseError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame

from custom_components.miele_lan.const import LAUNDRY_FAMILY, OVEN_FAMILY, MieleAppliance
from custom_components.miele_lan.coordinator import MieleLanCoordinator
from custom_components.miele_lan.dop2 import parse_hours_of_operation
from custom_components.miele_lan.sensor import DOP2_SENSORS

WCR890_BEFORE = bytes.fromhex(
    "002d000200770000000000050001080001710500020800000000000308000000000004080000000000050800000000"
)
WCR890_AFTER_LOAD_1 = bytes.fromhex(
    "002d00020077000000000005000108000171cf00020800000000000308000000000004080000000000050800000000"
)
WCR890_AFTER_LOAD_2 = bytes.fromhex(
    "002d000200770000000000050001080001729300020800000000000308000000000004080000000000050800000000"
)
TCR870WP_BEFORE = bytes.fromhex("0018000200770000000000020001080001014400020800000000")
TCR870WP_AFTER = bytes.fromhex("001800020077000000000002000108000101e400020800000000")


def _hours_sensor():
    matches = [d for d in DOP2_SENSORS if d.description.key == "hours_of_operation"]
    assert len(matches) == 1
    return matches[0]


# --- parser -----------------------------------------------------------------

def test_washer_counter_grows_by_programme_minutes() -> None:
    before = parse_hours_of_operation(WCR890_BEFORE)
    after_1 = parse_hours_of_operation(WCR890_AFTER_LOAD_1)
    after_2 = parse_hours_of_operation(WCR890_AFTER_LOAD_2)
    assert before["total"] == 94469
    assert after_1["total"] - before["total"] == 202
    assert after_2["total"] - after_1["total"] == 196
    # The other four attributes are zero on this washer.
    assert all(v == 0 for k, v in before.items() if k != "total")


def test_dryer_counter_grows_by_programme_minutes() -> None:
    """The dryer only serves attributes 1 and 2."""
    before = parse_hours_of_operation(TCR870WP_BEFORE)
    after = parse_hours_of_operation(TCR870WP_AFTER)
    assert before == {"total": 65860, "before_replacement": 0}
    assert after["total"] - before["total"] == 160


# --- sensor -----------------------------------------------------------------

def test_hours_sensor_covers_ovens_and_laundry() -> None:
    d = _hours_sensor()
    assert set(OVEN_FAMILY) <= set(d.types)
    assert set(LAUNDRY_FAMILY) <= set(d.types)


def test_hours_sensor_converts_washer_minutes_to_hours() -> None:
    d = _hours_sensor()
    data = {"hours_of_operation": parse_hours_of_operation(WCR890_BEFORE)}
    assert d.description.value_fn(data) == 1574.5


# --- coordinator --------------------------------------------------------------

class _Raw:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.body = body
        self.resources: list[str] = []

    async def _request_bytes(self, method: str, resource: str, allowed_status=(200,), **kwargs):
        self.resources.append(resource)
        if self.status not in allowed_status:
            raise ResponseError(self.status, f"API error for {resource}")
        return self.status, self.body


@pytest.fixture
def make_coordinator(tmp_path, monkeypatch):
    monkeypatch.setattr(frame, "report_usage", lambda *args, **kwargs: None)

    def make(device_type: MieleAppliance, raw: _Raw) -> MieleLanCoordinator:
        coord = MieleLanCoordinator(
            HomeAssistant(str(tmp_path)),
            SimpleNamespace(entry_id="test"),
            SimpleNamespace(raw=raw, wake=AsyncMock(return_value={})),
            "000000000001",
        )
        coord._data.ident = {"device_type": int(device_type)}
        return coord

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("device_type", "payload", "total"),
    [
        (MieleAppliance.WASHING_MACHINE, WCR890_BEFORE, 94469),
        (MieleAppliance.TUMBLE_DRYER, TCR870WP_BEFORE, 65860),
    ],
)
async def test_laundry_reads_hours_of_operation(make_coordinator, device_type, payload, total):
    coord = make_coordinator(device_type, _Raw(200, payload))
    await coord._maybe_refresh_hours_of_operation()
    assert coord.client.raw.resources == ["/Devices/000000000001/DOP2/2/119"]
    assert coord._data.dop2["hours_of_operation"]["total"] == total
    assert coord.hours_of_operation_supported


@pytest.mark.asyncio
async def test_sleeping_washer_500_is_transient_and_not_woken(make_coordinator):
    """A washer in standby answers 500. That must neither latch the leaf off
    nor send anything else (waking switches a washer on, #65)."""
    coord = make_coordinator(MieleAppliance.WASHING_MACHINE, _Raw(500))
    await coord._maybe_refresh_hours_of_operation()
    assert coord.client.raw.resources == ["/Devices/000000000001/DOP2/2/119"]
    coord.client.wake.assert_not_awaited()
    assert coord.hours_of_operation_supported
    assert "hours_of_operation" not in coord._data.dop2
