"""Pure helpers for the `dump_dop2_leaf` debug service.

No Home Assistant imports here — this module is exercised directly by
tests/test_dop2_dump.py without booting HA.
"""

from __future__ import annotations

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


# Resource names allowed for the generic `dump_rest_resource` debug service.
# Kept as an allow-list (rather than free-form path building) so the service
# can only ever GET plain-JSON resources that are safe to paste into an issue
# — never a DOP2 leaf (use `dump_dop2_leaf` for that) and never a
# write-capable path.
REST_RESOURCE_INDEX = "Index"
ALLOWED_REST_RESOURCES: tuple[str, ...] = (
    "Ident", "State", "State.ExtendedState", REST_RESOURCE_INDEX,
)


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
    that answers 404 on every DOP2 leaf.
    """
    if resource_name not in ALLOWED_REST_RESOURCES:
        raise ValueError(f"unsupported resource: {resource_name!r}")
    if resource_name == REST_RESOURCE_INDEX:
        return f"/Devices/{fab}"
    return f"/Devices/{fab}/{resource_name}"
