"""Regression tests for the DOP2 2/1585 nested-struct parser."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.dop2 import parse_global_device_context  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "wtw870_dop2_1585.hex"


def _load_fixture() -> bytes:
    content = FIXTURE.read_text()
    return bytes.fromhex(content.replace("\n", " ").replace(" ", ""))


def test_dos_containers_present() -> None:
    ctx = parse_global_device_context(_load_fixture())
    containers = ctx["dos_containers"]
    assert containers is not None
    assert len(containers) == 2


def test_container_1_values() -> None:
    ctx = parse_global_device_context(_load_fixture())
    c = ctx["dos_containers"][0]
    assert c["current_dosage_ml"] == 120
    assert c["filling_level_pct"] == 100
    assert c["bitmask_inserted"] is True


def test_container_2_values() -> None:
    ctx = parse_global_device_context(_load_fixture())
    c = ctx["dos_containers"][1]
    assert c["current_dosage_ml"] == 115
    assert c["filling_level_pct"] == 100
    assert c["bitmask_inserted"] is True


def test_device_state_decoded() -> None:
    ctx = parse_global_device_context(_load_fixture())
    ds = ctx["device_state"]
    assert ds is not None
    assert "appliance_state" in ds
    assert "operation_state" in ds
    assert "process_state" in ds


def test_wash2dry_state_absent_when_not_active() -> None:
    ctx = parse_global_device_context(_load_fixture())
    assert ctx["wash2dry_state"] is None


def test_empty_payload_returns_empty_dict() -> None:
    assert parse_global_device_context(b"") == {}


def test_short_payload_returns_empty_dict() -> None:
    assert parse_global_device_context(b"\x00" * 5) == {}
