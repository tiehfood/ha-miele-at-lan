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


def appliance_label(fab: str, name: str | None) -> str:
    """How the repair issue names an appliance: "Dishwasher (000100000002)".

    The fab number alone tells a user nothing about which appliance it is;
    it stays in brackets because it is what the logs and the Miele app use.
    """
    return f"{name} ({fab})" if name else fab


def issue_id(entry_id: str, fab: str) -> str:
    """One repair issue per appliance, so ignoring one sticks, and so an
    issue is only deleted once that appliance is really set up."""
    return f"{ISSUE_ID_PREFIX}{entry_id}_{fab}"


ISSUE_ID_PREFIX = "unenrolled_"
LEGACY_ISSUE_ID_PREFIX = "unenrolled_devices_"  # one issue listing every appliance


def issue_translation_placeholders(failed: FailedDevice, name: str | None) -> dict[str, str]:
    """Translation placeholders for one appliance's repair issue.

    `name` is the name Home Assistant shows for the appliance, or its type
    when it has never been set up.
    """
    return {
        "name": name or failed.fab,
        "appliance": appliance_label(failed.fab, name),
        "reason": failed.reason,
    }


def refine_failure_reason(
    original_reason: str,
    *,
    advertised_group: str | None,
    our_group: str,
) -> str:
    """Sharpen a generic enrollment failure reason with what mDNS actually saw.

    `advertised_group` is the `group=` TXT value read from the appliance's
    `_mieleathome._tcp` or `_mielesemiprof._tcp` service, or `None` if the appliance wasn't found on
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
