"""Tests for `MieleLanOptionsFlow` itself.

`async_create_entry(data=...)` replaces `entry.options` wholesale, so both
form steps must resubmit every option key on every save or the other one
gets silently dropped. See `options_validation.py` for the pure merge/parse
helpers exercised in isolation by `test_options_validation.py`.
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.config_flow import MieleLanOptionsFlow  # noqa: E402
from custom_components.miele_lan.const import (  # noqa: E402
    CONF_ADVERTISE_ADDRESS,
    CONF_DEVICES,
    CONF_STATIC_IPS,
    STATIC_IPS_TEXT_FIELD,
)

FAB = "000000000010"
STATIC_IP = "203.0.113.20"
ADVERTISE_ADDR = "203.0.113.99"


class _FakeConfigEntry:
    def __init__(self, *, data: dict, options: dict) -> None:
        self.data = data
        self.options = options


def _entry() -> _FakeConfigEntry:
    return _FakeConfigEntry(
        data={CONF_DEVICES: [{"fabNr": FAB, "deviceType": 1, "deviceName": "Oven"}]},
        options={CONF_STATIC_IPS: {FAB: STATIC_IP}, CONF_ADVERTISE_ADDRESS: ADVERTISE_ADDR},
    )


def _flow(monkeypatch, entry: _FakeConfigEntry) -> MieleLanOptionsFlow:
    flow = MieleLanOptionsFlow()
    monkeypatch.setattr(
        MieleLanOptionsFlow, "config_entry", property(lambda self: entry)
    )
    return flow


# ------------------------------------------------------------- known_devices
def test_known_devices_resubmit_keeps_both_options(monkeypatch) -> None:
    flow = _flow(monkeypatch, _entry())

    result = asyncio.run(flow.async_step_known_devices(
        {CONF_ADVERTISE_ADDRESS: ADVERTISE_ADDR, FAB: STATIC_IP}
    ))

    assert result["data"] == {
        CONF_STATIC_IPS: {FAB: STATIC_IP},
        CONF_ADVERTISE_ADDRESS: ADVERTISE_ADDR,
    }


def test_known_devices_empty_address_clears_it_but_keeps_static_ips(
    monkeypatch,
) -> None:
    flow = _flow(monkeypatch, _entry())

    result = asyncio.run(flow.async_step_known_devices(
        {CONF_ADVERTISE_ADDRESS: "", FAB: STATIC_IP}
    ))

    assert result["data"] == {CONF_STATIC_IPS: {FAB: STATIC_IP}}
    assert CONF_ADVERTISE_ADDRESS not in result["data"]


# ----------------------------------------------------------------- freeform
def test_freeform_resubmit_keeps_both_options(monkeypatch) -> None:
    flow = _flow(monkeypatch, _entry())

    result = asyncio.run(flow.async_step_freeform(
        {
            STATIC_IPS_TEXT_FIELD: f"{FAB}={STATIC_IP}",
            CONF_ADVERTISE_ADDRESS: ADVERTISE_ADDR,
        }
    ))

    assert result["data"] == {
        CONF_STATIC_IPS: {FAB: STATIC_IP},
        CONF_ADVERTISE_ADDRESS: ADVERTISE_ADDR,
    }


def test_freeform_empty_address_clears_it_but_keeps_static_ips(
    monkeypatch,
) -> None:
    flow = _flow(monkeypatch, _entry())

    result = asyncio.run(flow.async_step_freeform(
        {STATIC_IPS_TEXT_FIELD: f"{FAB}={STATIC_IP}", CONF_ADVERTISE_ADDRESS: ""}
    ))

    assert result["data"] == {CONF_STATIC_IPS: {FAB: STATIC_IP}}
    assert CONF_ADVERTISE_ADDRESS not in result["data"]
