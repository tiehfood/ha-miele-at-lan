"""Pure-Python tests for the Dop1 hood request builders. No HA / network.

The hex strings asserted here are the ones the appliance actually accepts, so
they double as a record of the wire format documented in const.py.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.api import (  # noqa: E402
    build_dop1_fan_level_request,
    build_dop1_main_light_request,
    build_dop1_run_on_time_request,
    build_dop1_setting_pf_read_request,
    build_dop1_setting_pf_write_request,
    parse_dop1_setting_pf_value,
)
from custom_components.miele_lan.const import (  # noqa: E402
    PF_DA_FETTFILTER_GRENZE_AKTUELL,
    RUN_ON_TIME_MINUTES,
)


# --- ventilation ------------------------------------------------------------

def test_fan_level_matches_capture() -> None:
    # 8E01 + write(02) + 00 + [version=0, selection=0xFF, level, power=0]
    assert build_dop1_fan_level_request(0) == "8E010200" + "00FF0000"
    assert build_dop1_fan_level_request(1) == "8E010200" + "00FF0100"
    assert build_dop1_fan_level_request(4) == "8E010200" + "00FF0400"


def test_fan_level_section_is_four_bytes() -> None:
    for level in range(6):
        assert len(build_dop1_fan_level_request(level)) == 8 + 8


def test_fan_level_rejects_out_of_range() -> None:
    for bad in (-1, 6, 255):
        with pytest.raises(ValueError):
            build_dop1_fan_level_request(bad)


# --- run-on time ------------------------------------------------------------

def test_run_on_time_matches_capture() -> None:
    # 8E06 + write(02) + 00 + [version=0, minutes]
    assert build_dop1_run_on_time_request(0) == "8E060200" + "0000"
    assert build_dop1_run_on_time_request(5) == "8E060200" + "0005"
    assert build_dop1_run_on_time_request(15) == "8E060200" + "000F"


def test_run_on_time_rejects_values_the_appliance_does_not_offer() -> None:
    for bad in (1, 10, 20, -5):
        assert bad not in RUN_ON_TIME_MINUTES
        with pytest.raises(ValueError):
            build_dop1_run_on_time_request(bad)


# --- main light -------------------------------------------------------------

def test_main_light_on_matches_capture() -> None:
    # 1400 + write(02) + 00 + [version=2, source=1, mode=4(cooking),
    #                          RGB=0, WW=0xFFFF, KW=0]
    assert build_dop1_main_light_request(True) == "14000200" + "020104000000000000FFFF0000"


def test_main_light_off_matches_capture() -> None:
    # mode=0 (off) and WW dimmed back to 0
    assert build_dop1_main_light_request(False) == "14000200" + "02010000000000000000000000"


def test_main_light_section_is_thirteen_bytes() -> None:
    for on in (True, False):
        assert len(build_dop1_main_light_request(on)) - 8 == 13 * 2


# --- Programmierfunktion settings ------------------------------------------

def test_setting_pf_write_matches_capture() -> None:
    # 1201 + write(02) + 00 + [version=0, PF_ID u16 BE, value u32 BE]
    # 40010 == 0x9C4A
    assert (
        build_dop1_setting_pf_write_request(PF_DA_FETTFILTER_GRENZE_AKTUELL, 1)
        == "12010200" + "009C4A00000001"
    )


def test_setting_pf_read_matches_capture() -> None:
    # Read request carries only version + PF_ID; note the read type byte (01).
    assert (
        build_dop1_setting_pf_read_request(PF_DA_FETTFILTER_GRENZE_AKTUELL)
        == "12010100" + "009C4A"
    )


def test_setting_pf_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        build_dop1_setting_pf_write_request(0x10000, 0)
    with pytest.raises(ValueError):
        build_dop1_setting_pf_write_request(1, 0x1_0000_0000)
    with pytest.raises(ValueError):
        build_dop1_setting_pf_read_request(-1)


# --- Programmierfunktion response parsing ----------------------------------

def _pf_response(pf_id: int, value: int, *, prefix: str = "12010100") -> str:
    """Build a read response: framing prefix + version, PF_ID, Wert, Min, Max."""
    return (
        f"{prefix}00{pf_id:04X}{value:08X}{0:08X}{4:08X}"
    )


def test_parse_reads_the_value_after_the_echoed_pf_id() -> None:
    assert parse_dop1_setting_pf_value(_pf_response(40010, 2), 40010) == 2
    assert parse_dop1_setting_pf_value(_pf_response(40011, 0), 40011) == 0


def test_parse_is_case_insensitive_and_tolerates_whitespace() -> None:
    hex_str = _pf_response(40010, 3)
    assert parse_dop1_setting_pf_value(f"  {hex_str.lower()}  ", 40010) == 3


def test_parse_returns_none_when_the_pf_is_absent() -> None:
    # Appliance answered about a different setting — no value for ours.
    assert parse_dop1_setting_pf_value(_pf_response(40011, 2), 40010) is None


def test_parse_returns_none_when_the_read_section_is_truncated() -> None:
    # PF_ID present but Wert/Min/Max don't fit — not a real read section.
    assert parse_dop1_setting_pf_value("009C4A0000", 40010) is None


def test_parse_skips_a_match_that_is_not_byte_aligned() -> None:
    # 0x9C4A appearing straddled across a byte boundary must not be mistaken
    # for the echoed PF_ID; the real, aligned occurrence wins.
    straddled = "F9C4A0" + _pf_response(40010, 7)
    assert parse_dop1_setting_pf_value(straddled, 40010) == 7


def test_parse_returns_none_on_odd_length_hex() -> None:
    assert parse_dop1_setting_pf_value("009C4A00000001F", 40010) is None
