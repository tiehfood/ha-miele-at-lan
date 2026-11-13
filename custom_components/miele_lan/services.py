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

from .api import build_program_select_payload
from .const import DOMAIN, PROGRAM_SELECT_LEAF, PROGRAM_SELECT_UNIT
from .dop2_dump import (
    ALLOWED_REST_RESOURCES,
    ANY_HTTP_STATUS,
    build_dop2_resource,
    build_rest_resource,
    bytes_to_hex,
)
from .dop2 import _walk_fields

_LOGGER = logging.getLogger(__name__)

SERVICE_DUMP_DOP2_LEAF = "dump_dop2_leaf"
SERVICE_DUMP_REST_RESOURCE = "dump_rest_resource"
SERVICE_SELECT_PROGRAM = "select_program"

ATTR_DEVICE_ID = "device_id"
ATTR_UNIT = "unit"
ATTR_ATTRIBUTE = "attribute"
ATTR_IDX1 = "idx1"
ATTR_IDX2 = "idx2"
ATTR_RESOURCE = "resource"
ATTR_PROGRAM_ID = "program_id"

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

DUMP_REST_RESOURCE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_RESOURCE): vol.In(ALLOWED_REST_RESOURCES),
    }
)

SELECT_PROGRAM_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_PROGRAM_ID): _DOP2_INDEX,
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


def _resolve_coordinator(hass: HomeAssistant, fab: str) -> Any:
    # Looked up by fab across loaded entries rather than via the device's
    # config entries: DeviceEntry.config_entries is deprecated (HA 2026.10)
    # and its replacement config_entry_id does not exist on older releases.
    for bundle in hass.data.get(DOMAIN, {}).values():
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
    coordinator = _resolve_coordinator(hass, fab)

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


async def _async_dump_rest_resource(call: ServiceCall) -> ServiceResponse:
    """Read-only GET of a plain REST resource (e.g. `/State`); never writes.

    Useful when a device's firmware doesn't implement DOP2 at all (every
    `dump_dop2_leaf` call 404s) but plain REST resources like `/State` still
    answer fine — e.g. to capture the raw `ExtendedState` hex blob for a hob
    model not yet covered by `extended_state.py`.
    """
    hass = call.hass
    device_id = call.data[ATTR_DEVICE_ID]
    resource_name = call.data[ATTR_RESOURCE]

    fab = _resolve_fab(hass, device_id)
    coordinator = _resolve_coordinator(hass, fab)

    resource = build_rest_resource(fab, resource_name)
    try:
        status, body = await coordinator.client.raw._request_bytes(
            "GET", resource, allowed_status=ANY_HTTP_STATUS,
        )
    except (NetworkConnectionError, NetworkTimeoutError) as err:
        raise HomeAssistantError(f"Could not reach the appliance: {err}") from err

    return {
        "resource": resource_name,
        "status": status,
        "length": len(body),
        "hex": bytes_to_hex(body),
        "text": body.decode("utf-8", errors="replace"),
    }


def _program_id(payload: bytes) -> int | None:
    fields, _ = _walk_fields(payload, 12, int.from_bytes(payload[10:12], "big"))
    value = fields.get(1, (None, None))[1]
    return value if isinstance(value, int) else None


async def _select_program(coordinator: Any, program_id: int) -> dict[str, Any]:
    """Read 2/1577, write it back with the new ProgId, and read it again.

    The write's HTTP status is returned rather than raised: this service
    exists to find out what each appliance accepts (#61).
    """
    resource = build_dop2_resource(
        coordinator.fab, PROGRAM_SELECT_UNIT, PROGRAM_SELECT_LEAF, 0, 0
    )
    raw = coordinator.client.raw
    try:
        status, before = await raw._request_bytes(
            "GET", resource, allowed_status=ANY_HTTP_STATUS,
        )
        if status != 200:
            raise HomeAssistantError(
                f"Reading DOP2 2/1577 failed (HTTP {status}); nothing was written."
            )
        try:
            payload = build_program_select_payload(before, program_id)
        except ValueError as err:
            raise HomeAssistantError(
                f"Unexpected DOP2 2/1577 layout ({err}); nothing was written. "
                f"Read: {bytes_to_hex(before)}"
            ) from err
        status, response = await raw._request_bytes(
            "PUT", resource, body=payload, allowed_status=ANY_HTTP_STATUS,
        )
        read_status, after = await raw._request_bytes(
            "GET", resource, allowed_status=ANY_HTTP_STATUS,
        )
    except (NetworkConnectionError, NetworkTimeoutError) as err:
        raise HomeAssistantError(f"Could not reach the appliance: {err}") from err

    return {
        "program_id_before": _program_id(before),
        "status": status,
        "response": response.decode("utf-8", errors="replace"),
        "written_hex": bytes_to_hex(payload),
        "read_status_after": read_status,
        "program_id_after": _program_id(after) if read_status == 200 else None,
        "hex_after": bytes_to_hex(after),
    }


async def _async_select_program(call: ServiceCall) -> ServiceResponse:
    """Experimental: select a programme via DOP2 2/1577. Never starts it."""
    fab = _resolve_fab(call.hass, call.data[ATTR_DEVICE_ID])
    coordinator = _resolve_coordinator(call.hass, fab)
    return await _select_program(coordinator, call.data[ATTR_PROGRAM_ID])


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register domain services once, regardless of how many entries call this."""
    if not hass.services.has_service(DOMAIN, SERVICE_DUMP_DOP2_LEAF):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DUMP_DOP2_LEAF,
            _async_dump_dop2_leaf,
            schema=DUMP_DOP2_LEAF_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_DUMP_REST_RESOURCE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DUMP_REST_RESOURCE,
            _async_dump_rest_resource,
            schema=DUMP_REST_RESOURCE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SELECT_PROGRAM):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SELECT_PROGRAM,
            _async_select_program,
            schema=SELECT_PROGRAM_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


@callback
def async_remove_services(hass: HomeAssistant) -> None:
    """Drop domain services once the last config entry has unloaded."""
    if hass.config_entries.async_loaded_entries(DOMAIN):
        return
    hass.services.async_remove(DOMAIN, SERVICE_DUMP_DOP2_LEAF)
    hass.services.async_remove(DOMAIN, SERVICE_DUMP_REST_RESOURCE)
    hass.services.async_remove(DOMAIN, SERVICE_SELECT_PROGRAM)
