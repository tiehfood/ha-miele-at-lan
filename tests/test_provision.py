"""Tests for the provisioning CLI's key generation + payload shapes."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import miele_lan_provision as prov  # type: ignore


def test_generate_keys_format() -> None:
    gid, gkey = prov.generate_keys()
    assert re.fullmatch(r"[0-9A-F]{16}", gid), gid
    assert re.fullmatch(r"[0-9A-F]{128}", gkey), gkey


def test_generate_keys_unique() -> None:
    seen = {prov.generate_keys() for _ in range(20)}
    assert len(seen) == 20  # all distinct (cryptographic randomness)


def test_redact_short() -> None:
    out = prov.redact("ABCD1234EFGH5678")
    assert "ABCD" in out and "5678" in out and "16 chars" in out
    assert "1234EFGH" not in out  # interior must be hidden


def test_redact_empty() -> None:
    assert prov.redact("") == ""
