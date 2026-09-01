"""Pure formatting/aggregation for enrollment results.

No aiohttp, no asyncmiele, no Home Assistant imports here — kept separate
from enrollment.py so the summary-line and repair-issue formatting can be
unit-tested without pulling in any of those.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FailedDevice:
    """An appliance the cloud knows about that enrollment could not reach."""

    fab: str
    reason: str


def summary_line(enrolled_count: int, failed: list[FailedDevice]) -> str:
    """One-line summary for the setup log.

    All succeeded:   "enrolled 3 of 3 appliances"
    Some missing:     "enrolled 2 of 3 appliances — missing 000000000000
                        (signed verify failed — wrong key, appliance offline,
                        or not paired into this household)"
    """
    total = enrolled_count + len(failed)
    if not failed:
        return f"enrolled {enrolled_count} of {total} appliances"
    missing = "; ".join(f"{f.fab} ({f.reason})" for f in failed)
    return f"enrolled {enrolled_count} of {total} appliances — missing {missing}"


def issue_translation_placeholders(failed: list[FailedDevice]) -> dict[str, str]:
    """Translation placeholders for the `unenrolled_devices` repair issue."""
    return {
        "count": str(len(failed)),
        "devices": "\n".join(f"{f.fab}: {f.reason}" for f in failed),
    }
