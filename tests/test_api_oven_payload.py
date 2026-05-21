"""Pure-Python tests for the oven UserRequest payload builder. No HA / network."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.api import build_oven_request_payload  # noqa: E402
from custom_components.miele_lan.const import (  # noqa: E402
    OPCODE_LIGHT_OFF,
    OPCODE_LIGHT_ON,
    OPCODE_SWITCH_OFF,
    OPCODE_SWITCH_ON,
)


def test_light_on_matches_capture() -> None:
    payload = build_oven_request_payload(OPCODE_LIGHT_ON)
    assert payload.hex() == "00100001062f000000000001000107000d202020202020202020202020202020"
    assert len(payload) == 32


def test_light_off_matches_capture() -> None:
    payload = build_oven_request_payload(OPCODE_LIGHT_OFF)
    assert payload.hex() == "00100001062f000000000001000107000e202020202020202020202020202020"


def test_switch_on_matches_capture() -> None:
    payload = build_oven_request_payload(OPCODE_SWITCH_ON)
    assert payload.hex() == "00100001062f0000000000010001070010202020202020202020202020202020"


def test_switch_off_matches_capture() -> None:
    payload = build_oven_request_payload(OPCODE_SWITCH_OFF)
    assert payload.hex() == "00100001062f0000000000010001070013202020202020202020202020202020"


def test_payload_is_aes_block_aligned() -> None:
    for op in (OPCODE_LIGHT_ON, OPCODE_LIGHT_OFF, OPCODE_SWITCH_ON, OPCODE_SWITCH_OFF):
        assert len(build_oven_request_payload(op)) % 16 == 0


def test_rejects_out_of_range_opcode() -> None:
    import pytest

    with pytest.raises(ValueError):
        build_oven_request_payload(-1)
    with pytest.raises(ValueError):
        build_oven_request_payload(256)


def test_state_pad_alignment_round_trip() -> None:
    """Padding must round to 16-byte AES boundary; JSON keeps closing brace."""
    from custom_components.miele_lan.api import _pad_request_body

    for raw in (b'{"Light":1}', b'{"DeviceAction":2}',
                b'{"Resource":"/Devices/000192453012/State/","Callback":"http://192.168.33.53:8765/event"}'):
        out = _pad_request_body(raw)
        assert len(out) % 16 == 0
        assert out.endswith(b"}")
        # Content preserved (just spaces inserted before })
        no_space = out.rstrip(b"\x20")
        assert no_space.endswith(b"}")
        assert raw[:-1] == no_space[:len(raw) - 1]
