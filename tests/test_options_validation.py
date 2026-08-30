"""Tests for the static-IP options-flow parsing/validation.

`options_validation.py` is loaded straight off disk, same as `cloud.py` in
test_cloud_regions.py — it has zero relative imports, so this stays free of
any `homeassistant` dependency.
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_options_validation",
    ROOT / "custom_components" / "miele_lan" / "options_validation.py",
)
ov = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_options_validation"] = ov
_spec.loader.exec_module(ov)


FAB = "000000000000"
OTHER_FAB = "000000000001"
IP = "192.0.2.1"
OTHER_IP = "192.0.2.2"


# --------------------------------------------------------------- is_valid_fab
def test_valid_fab_is_twelve_digits() -> None:
    assert ov.is_valid_fab(FAB) is True


def test_fab_too_short_is_invalid() -> None:
    assert ov.is_valid_fab("123") is False


def test_fab_too_long_is_invalid() -> None:
    assert ov.is_valid_fab(FAB + "9") is False


def test_fab_with_non_digits_is_invalid() -> None:
    assert ov.is_valid_fab("00000000000a") is False


# -------------------------------------------------------------- is_valid_ipv4
def test_valid_ipv4_accepted() -> None:
    assert ov.is_valid_ipv4(IP) is True


def test_ipv6_is_rejected() -> None:
    assert ov.is_valid_ipv4("::1") is False


def test_garbage_is_rejected() -> None:
    assert ov.is_valid_ipv4("not-an-ip") is False


def test_out_of_range_octet_is_rejected() -> None:
    assert ov.is_valid_ipv4("192.0.2.999") is False


# ------------------------------------------------------ merge_known_device_fields
def test_merge_sets_a_new_ip() -> None:
    new, invalid = ov.merge_known_device_fields({}, {FAB: IP})
    assert new == {FAB: IP}
    assert invalid == []


def test_merge_changes_an_existing_ip() -> None:
    new, invalid = ov.merge_known_device_fields({FAB: IP}, {FAB: OTHER_IP})
    assert new == {FAB: OTHER_IP}
    assert invalid == []


def test_merge_clears_on_blank_value() -> None:
    new, invalid = ov.merge_known_device_fields({FAB: IP}, {FAB: ""})
    assert new == {}
    assert invalid == []


def test_merge_clears_on_whitespace_only_value() -> None:
    new, invalid = ov.merge_known_device_fields({FAB: IP}, {FAB: "   "})
    assert new == {}
    assert invalid == []


def test_merge_rejects_invalid_ip_and_leaves_it_untouched() -> None:
    new, invalid = ov.merge_known_device_fields({FAB: IP}, {FAB: "not-an-ip"})
    assert new == {FAB: IP}
    assert invalid == [FAB]


def test_merge_leaves_untouched_fabs_alone() -> None:
    new, invalid = ov.merge_known_device_fields(
        {FAB: IP, OTHER_FAB: OTHER_IP}, {FAB: OTHER_IP}
    )
    assert new == {FAB: OTHER_IP, OTHER_FAB: OTHER_IP}
    assert invalid == []


# ------------------------------------------------------------ parse_static_ip_lines
def test_parse_single_pair() -> None:
    mapping, bad = ov.parse_static_ip_lines(f"{FAB}={IP}")
    assert mapping == {FAB: IP}
    assert bad == []


def test_parse_multiple_pairs() -> None:
    mapping, bad = ov.parse_static_ip_lines(f"{FAB}={IP}\n{OTHER_FAB}={OTHER_IP}")
    assert mapping == {FAB: IP, OTHER_FAB: OTHER_IP}
    assert bad == []


def test_parse_ignores_blank_lines() -> None:
    mapping, bad = ov.parse_static_ip_lines(f"\n{FAB}={IP}\n\n")
    assert mapping == {FAB: IP}
    assert bad == []


def test_parse_strips_whitespace_around_pairs() -> None:
    mapping, bad = ov.parse_static_ip_lines(f"  {FAB} = {IP}  ")
    assert mapping == {FAB: IP}
    assert bad == []


def test_parse_rejects_line_without_equals() -> None:
    mapping, bad = ov.parse_static_ip_lines(FAB)
    assert mapping == {}
    assert bad == [FAB]


def test_parse_rejects_bad_fab() -> None:
    line = f"123={IP}"
    mapping, bad = ov.parse_static_ip_lines(line)
    assert mapping == {}
    assert bad == [line]


def test_parse_rejects_bad_ip() -> None:
    line = f"{FAB}=not-an-ip"
    mapping, bad = ov.parse_static_ip_lines(line)
    assert mapping == {}
    assert bad == [line]


def test_parse_empty_text_clears_everything() -> None:
    mapping, bad = ov.parse_static_ip_lines("")
    assert mapping == {}
    assert bad == []
