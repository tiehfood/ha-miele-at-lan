"""Rendering for the raw `/State` debug-log lines.

No Home Assistant imports — kept separate from coordinator.py so the
rendering (stable ordering, size cap, truncation marker) can be
unit-tested directly, same pattern as polling.py and enrollment_report.py.
"""

from __future__ import annotations

import json
from typing import Any

STATE_LOG_MAX_CHARS = 4000


def render_state_for_log(state: dict[str, Any], *, max_chars: int = STATE_LOG_MAX_CHARS) -> str:
    """Render a `/State` (or push Content) dict for a debug-log line.

    Keys are sorted so two renders taken at different moments can be diffed
    by eye, and the result is capped at `max_chars` so a pathological
    payload can't dump unbounded text into someone's log — the cut is
    marked explicitly rather than silently truncating mid-value.
    """
    rendered = json.dumps(state, sort_keys=True, default=str)
    if len(rendered) <= max_chars:
        return rendered
    return f"{rendered[:max_chars]}...<truncated, {len(rendered)} chars total>"


__all__ = ["STATE_LOG_MAX_CHARS", "render_state_for_log"]
