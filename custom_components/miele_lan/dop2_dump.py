"""Pure helpers for the `dump_dop2_leaf` debug service.

No Home Assistant imports here — this module is exercised directly by
tests/test_dop2_dump.py without booting HA.
"""

from __future__ import annotations

# Every HTTP status a device might plausibly answer with. The dump service is
# explicitly interested in the non-200 cases (403/404/500 tell a user whether
# their firmware exposes a leaf at all), so nothing here should raise.
ANY_HTTP_STATUS: tuple[int, ...] = tuple(range(100, 600))


def build_dop2_resource(
    fab: str, unit: int, attribute: int, idx1: int = 0, idx2: int = 0
) -> str:
    """Build the DOP2 leaf resource path, matching every other call site."""
    return f"/Devices/{fab}/DOP2/{unit}/{attribute}?idx1={idx1}&idx2={idx2}"


def bytes_to_hex(data: bytes) -> str:
    """Lowercase hex dump of a leaf body, safe to paste into a GitHub issue."""
    return data.hex()
