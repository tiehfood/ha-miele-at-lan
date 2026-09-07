"""Tests for the raw `/State` debug-log rendering helper.

`state_debug_log.py` is loaded straight off disk, same as `polling.py` in
test_polling.py — it has zero relative imports, so this stays free of any
`homeassistant` dependency.
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location(
    "miele_lan_state_debug_log",
    ROOT / "custom_components" / "miele_lan" / "state_debug_log.py",
)
state_debug_log = importlib.util.module_from_spec(_spec)
sys.modules["miele_lan_state_debug_log"] = state_debug_log
_spec.loader.exec_module(state_debug_log)


def test_empty_dict_renders_sensibly() -> None:
    assert state_debug_log.render_state_for_log({}) == "{}"


def test_output_is_valid_json_round_trip() -> None:
    state = {"Status": {"value_raw": 5}, "RemainingTime": [0, 30]}
    rendered = state_debug_log.render_state_for_log(state)
    assert json.loads(rendered) == state


def test_key_order_is_stable_regardless_of_insertion_order() -> None:
    a = {"b": 1, "a": 2, "c": 3}
    b = {"c": 3, "a": 2, "b": 1}
    assert state_debug_log.render_state_for_log(a) == state_debug_log.render_state_for_log(b)


def test_key_order_is_sorted() -> None:
    state = {"z": 1, "a": 2, "m": 3}
    rendered = state_debug_log.render_state_for_log(state)
    assert rendered.index('"a"') < rendered.index('"m"') < rendered.index('"z"')


def test_small_payload_is_not_truncated() -> None:
    state = {"Status": {"value_raw": 5}}
    rendered = state_debug_log.render_state_for_log(state)
    assert "truncated" not in rendered


def test_oversized_payload_is_capped_and_marked() -> None:
    state = {"Payload": "x" * 10_000}
    rendered = state_debug_log.render_state_for_log(state, max_chars=100)
    assert len(rendered) > 100
    assert rendered.startswith('{"Payload": "' + "x" * 10)
    assert "...<truncated, " in rendered
    assert rendered.endswith(" chars total>")


def test_truncation_respects_custom_max_chars() -> None:
    state = {"a": "y" * 1000}
    rendered = state_debug_log.render_state_for_log(state, max_chars=50)
    body_before_marker = rendered.split("...<truncated,")[0]
    assert len(body_before_marker) == 50


def test_non_json_native_values_are_stringified() -> None:
    state = {"fab": "000000000000", "seen_at": object()}
    rendered = state_debug_log.render_state_for_log(state)
    assert "000000000000" in rendered
