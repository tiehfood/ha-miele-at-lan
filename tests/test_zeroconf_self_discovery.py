"""Our own push listener must never show up as a newly discovered appliance.

Zeroconf renames an instance whose name it believes is taken (an mDNS
repeater echoing our own announcement back is enough), e.g.
`HomeAssistant 000999000001` → `HomeAssistant 000999000001-2`. The zeroconf
step parsed a fab out of the name to recognise itself; with the `-2` the
digits became `009990000012`, and Home Assistant showed "Discovered: Miele
device 009990000012" — itself.
"""

import asyncio
import sys
from ipaddress import ip_address
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo  # noqa: E402

from custom_components.miele_lan.config_flow import MieleLanConfigFlow  # noqa: E402
from custom_components.miele_lan.const import (  # noqa: E402
    CONF_DEVICES,
    CONF_GROUP_ID,
    CONF_HA_FAB,
)
from custom_components.miele_lan.push_listener import DEFAULT_DEVICE_TYPE  # noqa: E402

GROUP = "0123456789ABCDEF"
HA_FAB = "000999000001"
TYPE = "_mieleathome._tcp.local."


class _Entry:
    entry_id = "entry1"
    data = {CONF_GROUP_ID: GROUP, CONF_HA_FAB: HA_FAB, CONF_DEVICES: []}


def _discover(name: str, devicetype: int) -> dict:
    flow = MieleLanConfigFlow()
    flow._async_current_entries = lambda include_ignore=True: [_Entry()]  # type: ignore[method-assign]
    info = ZeroconfServiceInfo(
        ip_address=ip_address("203.0.113.226"),
        ip_addresses=[ip_address("203.0.113.226")],
        port=18082,
        hostname="Miele-001D63FFFE000001.local.",
        type=TYPE,
        name=name,
        properties={"group": GROUP, "devicetype": str(devicetype)},
    )
    return asyncio.run(flow.async_step_zeroconf(info))


def test_renamed_own_listener_is_not_offered_as_a_new_appliance() -> None:
    result = _discover(f"HomeAssistant {HA_FAB}-2.{TYPE}", DEFAULT_DEVICE_TYPE)

    assert result["type"] == "abort"
    assert result["reason"] == "not_miele"


def test_another_installs_listener_is_not_offered_either() -> None:
    result = _discover(f"HomeAssistant 000999000077.{TYPE}", DEFAULT_DEVICE_TYPE)

    assert result["type"] == "abort"
    assert result["reason"] == "not_miele"


def test_real_appliance_in_the_household_is_still_already_configured() -> None:
    result = _discover(f"Miele WWV980.{TYPE}", 1)

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
