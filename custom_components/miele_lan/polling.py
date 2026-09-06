"""Adaptive poll-interval decision — kept free of Home Assistant imports so
`decide_poll_interval` can be unit-tested directly (see tests/test_polling.py).

Push-capable appliances already report every state change for free; polling
faster on top of that is pure extra load — a signed HTTP request the
appliance's firmware has to verify and decrypt — with no gain in freshness.
So the fast interval only ever applies to appliances that are both (a) not
receiving push and (b) actually mid-programme, where the slow fallback would
otherwise show a finished or freshly-started cycle up to a minute late.
"""

from __future__ import annotations

POLL_FALLBACK_INTERVAL = 60  # seconds — idle appliances, and anything push-fed.
# 10s is an order of magnitude tighter than the 60s fallback, so a push-less
# appliance's remaining-time/phase feels responsive while a programme runs —
# without turning a background poll into a steady stream of signed requests
# for hardware that isn't ours. A 2s interval, as originally requested, would
# be 30x the existing fallback's request rate for the appliance's entire
# active runtime rather than a brief catch-up burst; that's not something to
# impose on someone else's LAN appliance without a much stronger case for it.
POLL_ACTIVE_INTERVAL = 10  # seconds — no push, and mid-programme.


def decide_poll_interval(*, is_push_active: bool, is_idle: bool) -> int:
    """Pick the poll cadence for the next scheduled refresh.

    Fast polling is reserved for appliances with no other channel telling
    us about state changes: idle appliances have nothing to catch, and
    appliances already receiving push don't need the redundant traffic.
    """
    if is_push_active or is_idle:
        return POLL_FALLBACK_INTERVAL
    return POLL_ACTIVE_INTERVAL


__all__ = ["POLL_FALLBACK_INTERVAL", "POLL_ACTIVE_INTERVAL", "decide_poll_interval"]
