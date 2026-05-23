"""Tiny DOP2 binary leaf parser.

Implements just enough of the DOP2 TLV structure to extract specific
attribute fields from known leaves on the oven. We do NOT aim to be a
general-purpose DOP2 parser — asyncmiele covers more of that space but
its registered parsers are stub implementations for many leaves.

Wire format observed live on H7560BP (firmware 09.14, EK057S) — matches
the RootNode encoding in akappner/dop2rs:

    Root header (12 bytes):
        u16  declared length (bytes that follow this u16)
        u16  unit
        u16  attribute
        u16  idx1                  (struct iterator index, 0 for atomic)
        u16  idx2                  (struct iterator end,   0 for atomic)
        u8   struct-tag            (0x00 on every leaf we've seen)
        u8   attribute count

    Each attribute (variable):
        u16  index
        u8   type
        ...  type-dependent value bytes

    Type codes (matched against akappner/dop2rs Dop2PayloadsKind enum):
        0x01 Bool (1B)   0x02 U8  (1B)   0x03 I8  (1B)   0x04 E8  (1B)
        0x05 U16  (2B)   0x06 I16 (2B)   0x07 E16 (2B)
        0x08 U32  (4B)   0x09 I32 (4B)   0x0a E32 (4B)
        0x0b U64  (8B)   0x0c I64 (8B)   0x0d E64 (8B)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class Dop2Attribute:
    index: int
    type_code: int
    value: int | bool | None


_FIXED_SIZE_TYPES: dict[int, tuple[int, str]] = {
    0x01: (1, ">?"),   # Bool
    0x02: (1, ">B"),   # U8
    0x03: (1, ">b"),   # I8
    0x04: (1, ">B"),   # E8 (enum, treated as u8)
    0x05: (2, ">H"),   # U16
    0x06: (2, ">h"),   # I16
    0x07: (2, ">H"),   # E16
    0x08: (4, ">I"),   # U32
    0x09: (4, ">i"),   # I32
    0x0a: (4, ">I"),   # E32
    0x0b: (8, ">Q"),   # U64
    0x0c: (8, ">q"),   # I64
    0x0d: (8, ">Q"),   # E64
}


def parse_simple_leaf(payload: bytes) -> tuple[int, int, list[Dop2Attribute]]:
    """Decode a DOP2 root leaf consisting of fixed-size tagged scalars.

    Returns (unit, attribute, attributes). Stops at the first variable-length
    field (struct / array / string) — those require a richer walker.
    """
    if len(payload) < 12:
        raise ValueError(f"payload too short ({len(payload)} bytes)")
    unit = struct.unpack_from(">H", payload, 2)[0]
    attribute = struct.unpack_from(">H", payload, 4)[0]
    # offsets 6-9 are idx1/idx2 (struct iterator), 10-11 is u16 declared_fields.
    count = struct.unpack_from(">H", payload, 10)[0]
    attrs: list[Dop2Attribute] = []
    off = 12
    for _ in range(count):
        if off + 3 > len(payload):
            break
        idx = struct.unpack_from(">H", payload, off)[0]
        t = payload[off + 2]
        off += 3
        size_fmt = _FIXED_SIZE_TYPES.get(t)
        if size_fmt is None:
            attrs.append(Dop2Attribute(index=idx, type_code=t, value=None))
            break
        size, fmt = size_fmt
        if off + size > len(payload):
            break
        v = struct.unpack_from(fmt, payload, off)[0]
        off += size
        attrs.append(Dop2Attribute(index=idx, type_code=t, value=v))
    return unit, attribute, attrs


# ---------------------------------------------------------------------------
# Per-leaf convenience helpers (semantic interpretation)
# ---------------------------------------------------------------------------

def parse_hours_of_operation(payload: bytes) -> dict[str, int]:
    """DOP2 2/119 — `CS_HoursOfOperation` (akappner annotation).

    Attribute layout: 5 U32s indexed 1..5
      1 → hoursOfOperation
      2 → hoursOfOperationBeforeReplacement
      3 → hoursOfOperationSinceLastMaintenance
      4 → hoursOfOperationMode1
      5 → hoursOfOperationMode2

    Verified live on H7560BP: 3302 / 0 / 3302 / 0 / 0.
    """
    _, _, attrs = parse_simple_leaf(payload)
    labels = {
        1: "total",
        2: "before_replacement",
        3: "since_last_maintenance",
        4: "mode1",
        5: "mode2",
    }
    return {labels[a.index]: a.value for a in attrs
            if a.index in labels and isinstance(a.value, int)}
