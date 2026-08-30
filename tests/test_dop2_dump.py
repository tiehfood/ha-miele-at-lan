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
    ANY_HTTP_STATUS,
    build_dop2_resource,
    bytes_to_hex,
)
from custom_components.miele_lan.services import (  # noqa: E402
    ATTR_ATTRIBUTE,
    ATTR_DEVICE_ID,
    ATTR_IDX1,
    ATTR_IDX2,
    ATTR_UNIT,
    DUMP_DOP2_LEAF_SCHEMA,
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
