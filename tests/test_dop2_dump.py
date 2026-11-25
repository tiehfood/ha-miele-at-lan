"""Tests for the dump_dop2_leaf service's pure helpers and input schema.

No live HA core object needed: dop2_dump.py has zero HA imports, and
voluptuous schema validation runs standalone.
"""

import sys
from pathlib import Path

import pytest
import voluptuous as vol

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.dop2_dump import (  # noqa: E402
    ALLOWED_REST_RESOURCES,
    ANY_HTTP_STATUS,
    build_dop2_resource,
    build_rest_resource,
    bytes_to_hex,
)
from custom_components.miele_lan.services import (  # noqa: E402
    ATTR_ATTRIBUTE,
    ATTR_DEVICE_ID,
    ATTR_IDX1,
    ATTR_IDX2,
    ATTR_RESOURCE,
    ATTR_UNIT,
    DUMP_DOP2_LEAF_SCHEMA,
    DUMP_REST_RESOURCE_SCHEMA,
)


# ------------------------------------------------------------- resource path
def test_build_dop2_resource_matches_existing_call_sites() -> None:
    resource = build_dop2_resource("000000000000", 2, 1585)
    assert resource == "/Devices/000000000000/DOP2/2/1585?idx1=0&idx2=0"


def test_build_dop2_resource_with_explicit_indices() -> None:
    resource = build_dop2_resource("000000000000", 2, 105, idx1=3, idx2=7)
    assert resource == "/Devices/000000000000/DOP2/2/105?idx1=3&idx2=7"


# --------------------------------------------------------------------- hex
def test_bytes_to_hex_is_lowercase() -> None:
    assert bytes_to_hex(b"\xAB\xCD\x01") == "abcd01"


def test_bytes_to_hex_empty_body() -> None:
    assert bytes_to_hex(b"") == ""


# ------------------------------------------------------------ status range
def test_any_http_status_covers_the_interesting_failure_codes() -> None:
    for status in (200, 400, 403, 404, 500, 503):
        assert status in ANY_HTTP_STATUS


# ------------------------------------------------------------------ schema
def test_schema_requires_device_id_unit_attribute() -> None:
    with pytest.raises(vol.Invalid):
        DUMP_DOP2_LEAF_SCHEMA({ATTR_UNIT: 2, ATTR_ATTRIBUTE: 1585})


def test_schema_defaults_indices_to_zero() -> None:
    result = DUMP_DOP2_LEAF_SCHEMA(
        {ATTR_DEVICE_ID: "abc123", ATTR_UNIT: 2, ATTR_ATTRIBUTE: 1585}
    )
    assert result[ATTR_IDX1] == 0
    assert result[ATTR_IDX2] == 0


def test_schema_coerces_string_numbers() -> None:
    result = DUMP_DOP2_LEAF_SCHEMA(
        {ATTR_DEVICE_ID: "abc123", ATTR_UNIT: "2", ATTR_ATTRIBUTE: "1585"}
    )
    assert result[ATTR_UNIT] == 2
    assert result[ATTR_ATTRIBUTE] == 1585


def test_schema_rejects_out_of_range_unit() -> None:
    with pytest.raises(vol.Invalid):
        DUMP_DOP2_LEAF_SCHEMA(
            {ATTR_DEVICE_ID: "abc123", ATTR_UNIT: 999999, ATTR_ATTRIBUTE: 1585}
        )


# ------------------------------------------------------- dump_rest_resource
def test_build_rest_resource_state() -> None:
    assert (
        build_rest_resource("000000000000", "State")
        == "/Devices/000000000000/State"
    )


def test_build_rest_resource_ident() -> None:
    assert (
        build_rest_resource("000000000000", "Ident")
        == "/Devices/000000000000/Ident"
    )


def test_build_rest_resource_extended_state() -> None:
    assert (
        build_rest_resource("000000000000", "State.ExtendedState")
        == "/Devices/000000000000/State.ExtendedState"
    )


def test_build_rest_resource_index_is_the_device_root() -> None:
    assert build_rest_resource("000000000000", "Index") == "/Devices/000000000000"
    assert DUMP_REST_RESOURCE_SCHEMA(
        {ATTR_DEVICE_ID: "abc123", ATTR_RESOURCE: "Index"}
    )[ATTR_RESOURCE] == "Index"


def test_service_selector_offers_every_allowed_resource() -> None:
    # The Developer tools dropdown comes from services.yaml, the validation
    # from ALLOWED_REST_RESOURCES; a resource missing from either is unusable.
    import yaml

    options = yaml.safe_load(
        (ROOT / "custom_components/miele_lan/services.yaml").read_text()
    )["dump_rest_resource"]["fields"]["resource"]["selector"]["select"]["options"]
    assert tuple(options) == ALLOWED_REST_RESOURCES


def test_build_rest_resource_rejects_unknown_resource() -> None:
    with pytest.raises(ValueError):
        build_rest_resource("000000000000", "DOP2/2/1585")


def test_allowed_rest_resources_contains_state_and_ident() -> None:
    assert "State" in ALLOWED_REST_RESOURCES
    assert "Ident" in ALLOWED_REST_RESOURCES


def test_rest_resource_schema_requires_device_id_and_resource() -> None:
    with pytest.raises(vol.Invalid):
        DUMP_REST_RESOURCE_SCHEMA({ATTR_RESOURCE: "State"})


def test_rest_resource_schema_rejects_resource_outside_allow_list() -> None:
    with pytest.raises(vol.Invalid):
        DUMP_REST_RESOURCE_SCHEMA({ATTR_DEVICE_ID: "abc123", ATTR_RESOURCE: "DOP2/2/1585"})


def test_rest_resource_schema_accepts_known_resource() -> None:
    result = DUMP_REST_RESOURCE_SCHEMA({ATTR_DEVICE_ID: "abc123", ATTR_RESOURCE: "State"})
    assert result[ATTR_RESOURCE] == "State"


# ------------------------------------------------------ coordinator lookup
def test_resolve_coordinator_finds_fab_across_loaded_entries() -> None:
    from types import SimpleNamespace

    from custom_components.miele_lan.const import DOMAIN
    from custom_components.miele_lan.services import _resolve_coordinator

    coord = object()
    hass = SimpleNamespace(data={DOMAIN: {
        "entry_a": {"coordinators": {"000000000001": object()}},
        "entry_b": {"coordinators": {"000000000002": coord}},
    }})
    assert _resolve_coordinator(hass, "000000000002") is coord


def test_resolve_coordinator_raises_when_fab_not_loaded() -> None:
    from types import SimpleNamespace

    from homeassistant.exceptions import HomeAssistantError

    from custom_components.miele_lan.services import _resolve_coordinator

    with pytest.raises(HomeAssistantError):
        _resolve_coordinator(SimpleNamespace(data={}), "000000000002")
