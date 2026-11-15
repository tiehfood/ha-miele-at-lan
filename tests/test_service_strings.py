"""Every action in services.yaml has a translated name, description and fields."""
import json
from pathlib import Path

import pytest
import yaml

BASE = Path(__file__).resolve().parent.parent / "custom_components" / "miele_lan"
SERVICES = yaml.safe_load((BASE / "services.yaml").read_text())


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json", "translations/de.json"])
@pytest.mark.parametrize("service", sorted(SERVICES))
def test_service_is_translated(path: str, service: str) -> None:
    strings = json.loads((BASE / path).read_text())["services"][service]
    assert strings["name"] and strings["description"]
    assert set(strings["fields"]) == set(SERVICES[service]["fields"])
    assert all(f["name"] and f["description"] for f in strings["fields"].values())
