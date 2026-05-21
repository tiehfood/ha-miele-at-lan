"""Tests for the provisioning CLI's key generation + payload shapes.

The CLI lives under `tools/` which is gitignored (RE tooling, not shipped via
HACS). When running CI on a fresh checkout the module isn't available, so the
whole module is skipped instead of failing collection.
"""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

prov = pytest.importorskip(
    "miele_lan_provision",
    reason="tools/miele_lan_provision.py is gitignored; skip when not present locally",
)


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
