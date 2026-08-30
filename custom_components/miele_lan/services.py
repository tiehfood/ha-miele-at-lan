"""Domain-level services for Miele@LAN.

Registered once (guarded by `has_service`) from every `async_setup_entry`
call and removed once `async_unload_entry` sees no loaded entries left —
mirrors homeassistant.components.rainmachine's lifecycle so a reload of one
household entry never drops the service out from under another.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from asyncmiele.exceptions.network import NetworkConnectionError, NetworkTimeoutError
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import DOMAIN
from .dop2_dump import ANY_HTTP_STATUS, build_dop2_resource, bytes_to_hex

_LOGGER = logging.getLogger(__name__)

SERVICE_DUMP_DOP2_LEAF = "dump_dop2_leaf"

ATTR_DEVICE_ID = "device_id"
ATTR_UNIT = "unit"
ATTR_ATTRIBUTE = "attribute"
ATTR_IDX1 = "idx1"
ATTR_IDX2 = "idx2"

_DOP2_INDEX = vol.All(vol.Coerce(int), vol.Range(min=0, max=65535))

DUMP_DOP2_LEAF_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_UNIT): _DOP2_INDEX,
        vol.Required(ATTR_ATTRIBUTE): _DOP2_INDEX,
        vol.Optional(ATTR_IDX1, default=0): _DOP2_INDEX,
        vol.Optional(ATTR_IDX2, default=0): _DOP2_INDEX,
    }
)


def _resolve_fab(hass: HomeAssistant, device_id: str) -> str:
    device_entry = dr.async_get(hass).async_get(device_id)
    if device_entry is None:
        raise HomeAssistantError(f"Unknown device_id: {device_id}")
    fab = next(
        (ident[1] for ident in device_entry.identifiers if ident[0] == DOMAIN), None
    )
    if fab is None:
        raise HomeAssistantError("Selected device is not a Miele@LAN appliance")
    return fab


def _resolve_coordinator(hass: HomeAssistant, device_id: str, fab: str) -> Any:
    device_entry = dr.async_get(hass).async_get(device_id)
    for entry_id in device_entry.config_entries:
        bundle = hass.data.get(DOMAIN, {}).get(entry_id)
        if not bundle:
            continue
        coordinator = bundle.get("coordinators", {}).get(fab)
        if coordinator is not None:
            return coordinator
    raise HomeAssistantError(
        "This device has no active Miele@LAN connection right now — is the "
        "integration entry loaded and the appliance reachable on the LAN?"
    )


async def _async_dump_dop2_leaf(call: ServiceCall) -> ServiceResponse:
    """Read-only GET of a single DOP2 leaf; never writes, never raises on non-200."""
    hass = call.hass
    device_id = call.data[ATTR_DEVICE_ID]
    unit = call.data[ATTR_UNIT]
    attribute = call.data[ATTR_ATTRIBUTE]
    idx1 = call.data[ATTR_IDX1]
    idx2 = call.data[ATTR_IDX2]

    fab = _resolve_fab(hass, device_id)
    coordinator = _resolve_coordinator(hass, device_id, fab)

    resource = build_dop2_resource(fab, unit, attribute, idx1, idx2)
    try:
        status, body = await coordinator.client.raw._request_bytes(
            "GET", resource, allowed_status=ANY_HTTP_STATUS,
        )
    except (NetworkConnectionError, NetworkTimeoutError) as err:
        raise HomeAssistantError(f"Could not reach the appliance: {err}") from err

    return {
        "unit": unit,
        "attribute": attribute,
        "idx1": idx1,
        "idx2": idx2,
        "status": status,
        "length": len(body),
        "hex": bytes_to_hex(body),
    }


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register domain services once, regardless of how many entries call this."""
    if hass.services.has_service(DOMAIN, SERVICE_DUMP_DOP2_LEAF):
        return
    hass.services.async_register(
        DOMAIN,
        SERVICE_DUMP_DOP2_LEAF,
        _async_dump_dop2_leaf,
        schema=DUMP_DOP2_LEAF_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


@callback
def async_remove_services(hass: HomeAssistant) -> None:
    """Drop domain services once the last config entry has unloaded."""
    if hass.config_entries.async_loaded_entries(DOMAIN):
        return
    hass.services.async_remove(DOMAIN, SERVICE_DUMP_DOP2_LEAF)
