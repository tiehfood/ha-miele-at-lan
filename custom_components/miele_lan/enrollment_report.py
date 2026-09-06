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


def refine_failure_reason(
    original_reason: str,
    *,
    advertised_group: str | None,
    our_group: str,
) -> str:
    """Sharpen a generic enrollment failure reason with what mDNS actually saw.

    `advertised_group` is the `group=` TXT value read from the appliance's
    `_mieleathome._tcp` service, or `None` if the appliance wasn't found on
    mDNS at all. An empty (but present) group means the appliance has never
    been commissioned into any household — there is no key on its side to be
    right or wrong about, so the generic "wrong key / offline / not paired"
    reason is actively misleading in that case.
    """
    if advertised_group is None:
        return original_reason
    if advertised_group == "":
        return (
            "appliance is not commissioned into any household — reset "
            "Miele@home on the appliance, then reconnect it in the Miele app"
        )
    if advertised_group.upper() != our_group.upper():
        return (
            f"appliance belongs to household {advertised_group.upper()}, "
            f"but this entry is {our_group.upper()}"
        )
    return original_reason
