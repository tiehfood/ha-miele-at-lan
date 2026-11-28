"""Tests for the UpdateFailed message-naming helper.

`_describe_update_error` lives in `coordinator.py`, same as
`_merge_mdns_adoptions` in test_mdns_adoption.py for `__init__.py` — pure and
stubbable without touching HA's config-entry machinery.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.miele_lan.coordinator import _describe_update_error  # noqa: E402


def test_exception_with_empty_str_names_the_type() -> None:
    assert _describe_update_error(TimeoutError()) == "TimeoutError"


def test_exception_with_a_message_includes_type_and_message() -> None:
    err = ConnectionError("HTTP error 503: service unavailable")

    assert (
        _describe_update_error(err)
        == "ConnectionError: HTTP error 503: service unavailable"
    )


def _chained(outer: Exception, cause: BaseException) -> Exception:
    """`outer` raised `from cause`, the way api.py wraps aiohttp errors."""
    try:
        raise outer from cause
    except Exception as err:  # noqa: BLE001
        return err


def test_os_error_code_from_the_cause_chain_is_named() -> None:
    import errno

    import aiohttp
    from asyncmiele.exceptions.network import NetworkConnectionError

    os_error = OSError(errno.EHOSTUNREACH, "Connect call failed ('192.0.2.10', 80)")
    connector_error = aiohttp.ClientConnectorError(
        aiohttp.client_reqrep.ConnectionKey(
            "192.0.2.10", 80, False, True, None, None, None
        ),
        os_error,
    )
    err = _chained(NetworkConnectionError(str(connector_error)), connector_error)

    assert _describe_update_error(err).endswith(" [EHOSTUNREACH]")


def test_refused_and_unreachable_read_differently() -> None:
    import errno

    refused = _chained(ConnectionError("Cannot connect"), OSError(errno.ECONNREFUSED, "x"))
    unreachable = _chained(ConnectionError("Cannot connect"), OSError(errno.EHOSTUNREACH, "x"))

    assert _describe_update_error(refused) == "ConnectionError: Cannot connect [ECONNREFUSED]"
    assert _describe_update_error(unreachable) == "ConnectionError: Cannot connect [EHOSTUNREACH]"


def test_no_os_error_code_adds_nothing() -> None:
    # TimeoutError is an OSError subclass but carries no errno.
    assert _describe_update_error(TimeoutError()) == "TimeoutError"
