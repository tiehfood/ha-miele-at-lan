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

    Nested/container type codes:
        0x10 MStruct   — u16 field_count, then field_count × (u16 idx, u8 type, value)
        0x17 ArrayE16  — u16 element_count, then element_count × u16
        0x20 MString   — u16 byte_length, then byte_length bytes UTF-8
        0x21 AStruct   — u16 element_count, then element_count × (u16 field_count, fields...)
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


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
# Nested MStruct walker
# ---------------------------------------------------------------------------

def _walk_fields(buf: bytes, off: int, count: int) -> tuple[dict[int, tuple[int, Any]], int]:
    """Walk `count` DOP2 fields starting at `off`, returning (field_dict, new_off).

    field_dict maps field_index → (type_tag, decoded_value).
    Decoded value for MStruct is a nested dict; for AStruct a list of dicts;
    for ArrayE16 a list of ints; for MString a str; for scalars an int/bool.
    Unknown type tags are stored with value None and iteration continues.
    """
    result: dict[int, tuple[int, Any]] = {}
    for _ in range(count):
        if off + 3 > len(buf):
            break
        idx = struct.unpack_from(">H", buf, off)[0]
        t = buf[off + 2]
        off += 3

        if t in _FIXED_SIZE_TYPES:
            sz, fmt = _FIXED_SIZE_TYPES[t]
            if off + sz > len(buf):
                break
            v = struct.unpack_from(fmt, buf, off)[0]
            off += sz
            result[idx] = (t, v)

        elif t == 0x10:  # MStruct
            if off + 2 > len(buf):
                break
            inner_count = struct.unpack_from(">H", buf, off)[0]
            off += 2
            sub, off = _walk_fields(buf, off, inner_count)
            result[idx] = (t, sub)

        elif t == 0x21:  # AStruct
            if off + 2 > len(buf):
                break
            elem_count = struct.unpack_from(">H", buf, off)[0]
            off += 2
            elems: list[dict[int, tuple[int, Any]]] = []
            for _ in range(elem_count):
                if off + 2 > len(buf):
                    break
                fc = struct.unpack_from(">H", buf, off)[0]
                off += 2
                elem_dict, off = _walk_fields(buf, off, fc)
                elems.append(elem_dict)
            result[idx] = (t, elems)

        elif t == 0x17:  # ArrayE16
            if off + 2 > len(buf):
                break
            arr_len = struct.unpack_from(">H", buf, off)[0]
            off += 2
            arr: list[int] = []
            for _ in range(arr_len):
                if off + 2 > len(buf):
                    break
                arr.append(struct.unpack_from(">H", buf, off)[0])
                off += 2
            result[idx] = (t, arr)

        elif t == 0x20:  # MString
            if off + 2 > len(buf):
                break
            str_len = struct.unpack_from(">H", buf, off)[0]
            off += 2
            s = buf[off:off + str_len].decode("utf-8", errors="replace")
            off += str_len
            result[idx] = (t, s)

        else:
            result[idx] = (t, None)

    return result, off


def parse_struct(buf: bytes, off: int) -> dict[int, tuple[int, Any]]:
    """Walk a DOP2 MStruct body starting at byte offset `off`.

    Returns a dict mapping field_index → (type_tag, decoded_value).
    Suitable for calling on the stripped body of any leaf whose root
    type is MStruct (i.e. after stripping the 12-byte root header and
    reading the u16 field count).
    """
    if off + 2 > len(buf):
        return {}
    count = struct.unpack_from(">H", buf, off)[0]
    off += 2
    result, _ = _walk_fields(buf, off, count)
    return result


# ---------------------------------------------------------------------------
# Typed convenience helper for GLOBAL_DeviceContext (DOP2 2/1585)
# ---------------------------------------------------------------------------

_WASH2DRY_LABELS: dict[int, str] = {
    0: "not_active",
    1: "activatable",
    2: "active",
    3: "ready_to_receive",
    4: "program_received",
    5: "no_program_available",
}


def _decode_dos_container(c: dict[int, tuple[int, Any]]) -> dict[str, Any]:
    bitmask = c.get(2, (None, 0))[1] or 0
    filling_level_struct = c.get(6, (None, {}))[1] or {}
    fill_pct = (filling_level_struct.get(2, (None, None))[1]
                if isinstance(filling_level_struct, dict) else None)
    return {
        "bitmask_inserted": bool(bitmask & 0x01),
        "bitmask_empty": bool(bitmask & 0x02),
        "bitmask_supported": bool(bitmask & 0x04),
        "bitmask_active": bool(bitmask & 0x08),
        "bitmask_low_level": bool(bitmask & 0x10),
        "container_size_ml": c.get(3, (None, None))[1],
        "initial_dosage_ml": c.get(4, (None, None))[1],
        "current_dosage_ml": c.get(5, (None, None))[1],
        "filling_level_pct": fill_pct,
    }


def parse_global_device_context(payload: bytes) -> dict[str, Any]:
    """DOP2 2/1585 — GLOBAL_DeviceContext.

    Strips the 12-byte root header and walks the nested MStruct, returning
    a typed convenience dict with the fields relevant to sensor entities.
    Returns an empty dict on any structural error.
    """
    if len(payload) < 12:
        return {}
    root_count = struct.unpack_from(">H", payload, 10)[0]
    root, _ = _walk_fields(payload, 12, root_count)

    device_state: dict[str, int] | None = None
    f1 = root.get(1)
    if f1 and f1[0] == 0x10 and isinstance(f1[1], dict):
        inner = f1[1]
        device_state = {
            "appliance_state": inner.get(1, (None, 0))[1],
            "operation_state": inner.get(2, (None, 0))[1],
            "process_state": inner.get(3, (None, 0))[1],
        }

    prog: dict | None = None
    f5 = root.get(5)
    if f5 and f5[0] == 0x10 and isinstance(f5[1], dict):
        prog = f5[1]

    device_attributes: dict | None = None
    dos_containers: list[dict] | None = None

    f6 = root.get(6)
    if f6 and f6[0] == 0x10 and isinstance(f6[1], dict):
        device_attributes = f6[1]
        f7 = device_attributes.get(7)
        if f7 and f7[0] == 0x21 and isinstance(f7[1], list):
            dos_containers = [_decode_dos_container(c) for c in f7[1]]

    wash2dry_raw: int | None = None
    f16 = root.get(16)
    if f16 is not None:
        wash2dry_raw = f16[1]

    return {
        "device_state": device_state,
        "prog": prog,
        "device_attributes_dwtdwm": device_attributes,
        "dos_containers": dos_containers,
        "wash2dry_state": (
            _WASH2DRY_LABELS.get(wash2dry_raw) if wash2dry_raw is not None else None
        ),
    }


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
