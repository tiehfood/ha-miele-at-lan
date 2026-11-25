"""Pure helpers for the `dump_dop2_leaf` debug service.

No Home Assistant imports here — this module is exercised directly by
tests/test_dop2_dump.py without booting HA.
"""

from __future__ import annotations

import re

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


# What the `dump_rest_resource` debug service offers in its dropdown. Any other
# path below the appliance is accepted too, because a new protocol generation
# links resources nobody has seen yet from its index.
REST_RESOURCE_INDEX = "Index"
REST_RESOURCE_SUGGESTIONS: tuple[str, ...] = (
    "Ident", "State", "State.ExtendedState", REST_RESOURCE_INDEX,
)

_REST_PATH = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?")
_REST_PATH_MAX_LENGTH = 200


def rest_resource_name(value: object) -> str:
    """Validate the `resource` input of `dump_rest_resource`.

    The path stays below `/Devices/{fab}/`: the household key and the Wi-Fi
    credentials live on host-scoped resources, and a result of this service
    is meant to be pasted into a public issue. So no leading slash, no `..`
    segment, no query string and no percent-encoding.
    """
    if (
        not isinstance(value, str)
        or len(value) > _REST_PATH_MAX_LENGTH
        or not _REST_PATH.fullmatch(value)
        or any(not segment.strip(".") for segment in value.rstrip("/").split("/"))
    ):
        raise ValueError(f"unsupported resource: {value!r}")
    return value


def build_rest_resource(fab: str, resource_name: str) -> str:
    """Build a plain `/Devices/{fab}/{resource_name}` REST resource path.

    Some appliances (notably some hobs) don't implement DOP2 at all and
    answer 404 to every `/DOP2/...` leaf, while still answering fine on the
    plain REST resources such as `/State`. This lets users capture the raw
    JSON straight from the appliance for debugging — e.g. to see the actual
    length/shape of the hex-encoded `ExtendedState` blob on a model that
    isn't yet covered by `extended_state.py`.

    `Index` is the device root: the appliance's own list of the resources it
    serves, which is the first thing to look at on a protocol generation
    that answers 404 on every DOP2 leaf. The names it links can be read by
    passing them as `resource_name`, exactly as typed (a trailing slash is
    kept, since the index writes its links with one).
    """
    if resource_name == REST_RESOURCE_INDEX:
        return f"/Devices/{fab}"
    return f"/Devices/{fab}/{rest_resource_name(resource_name)}"
