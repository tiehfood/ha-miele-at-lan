"""Pure parsing/validation for the static-IP options flow.

No Home Assistant imports on purpose — this module is exercised directly by
`tests/test_options_validation.py` without pulling in `homeassistant`.
"""

from __future__ import annotations

import ipaddress

FAB_LENGTH = 12


def is_valid_fab(fab: str) -> bool:
    return len(fab) == FAB_LENGTH and fab.isdigit()


def is_valid_ipv4(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def merge_known_device_fields(
    current: dict[str, str], submitted: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Merge per-device options-form fields into `current`.

    `submitted` maps fabNr -> the raw string the user typed for that
    appliance's field. An empty/whitespace-only value clears that fab's
    entry. Returns `(new_mapping, invalid_fabs)` — fabs whose value failed
    IPv4 validation are left untouched in the result and reported back so the
    caller can show a form error.
    """
    result = dict(current)
    invalid: list[str] = []
    for fab, value in submitted.items():
        value = (value or "").strip()
        if not value:
            result.pop(fab, None)
            continue
        if not is_valid_ipv4(value):
            invalid.append(fab)
            continue
        result[fab] = value
    return result, invalid


def parse_static_ip_lines(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse free-form `fabNr=IP` pairs, one per line.

    Blank lines are ignored. Returns `(mapping, bad_lines)` — lines that
    aren't `<12-digit fab>=<IPv4>` are reported back verbatim so the caller
    can show a form error.
    """
    mapping: dict[str, str] = {}
    bad_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fab, sep, ip = line.partition("=")
        fab = fab.strip()
        ip = ip.strip()
        if not sep or not is_valid_fab(fab) or not is_valid_ipv4(ip):
            bad_lines.append(line)
            continue
        mapping[fab] = ip
    return mapping, bad_lines
