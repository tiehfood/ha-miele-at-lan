"""German drying step names as shown on Miele dryer panels.

Checked on a TMR840WP: DryingStep 1 reads Schranktrocken (plus) on the
panel (the translation said Normal plus).
"""
import json
from pathlib import Path

import pytest

DE = Path(__file__).parents[1] / "custom_components/miele_lan/translations/de.json"


@pytest.mark.parametrize("sensor", ["drying_step", "drying_step_raw"])
def test_german_drying_step_normal_plus(sensor):
    states = json.loads(DE.read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states["normal_plus"] == "Schranktrocken (plus)"
