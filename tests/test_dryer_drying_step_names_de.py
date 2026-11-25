"""German drying step names as shown on Miele dryer panels.

Checked on a TMR840WP: DryingStep 1 reads Schranktrocken (plus) and
DryingStep 2 reads Schranktrocken on the panel (the translations said
Normal plus and Normal).
"""
import json
from pathlib import Path

import pytest

DE = Path(__file__).parents[1] / "custom_components/miele_lan/translations/de.json"


@pytest.mark.parametrize("sensor", ["drying_step", "drying_step_raw"])
def test_german_drying_step_normal_plus(sensor):
    states = json.loads(DE.read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states["normal_plus"] == "Schranktrocken (plus)"


@pytest.mark.parametrize("sensor", ["drying_step", "drying_step_raw"])
def test_german_drying_step_normal(sensor):
    states = json.loads(DE.read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states["normal"] == "Schranktrocken"


# ProgramPhase 517 and 518 (tumble dryer and washer-dryer) dry to the same levels
@pytest.mark.parametrize("sensor", ["program_phase", "program_phase_raw"])
@pytest.mark.parametrize(
    ("key", "name"),
    [("normal", "Schranktrocken"), ("normal_plus", "Schranktrocken (plus)")],
)
def test_german_program_phase_normal(sensor, key, name):
    states = json.loads(DE.read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states[key] == name
