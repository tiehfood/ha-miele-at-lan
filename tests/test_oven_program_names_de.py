"""German names of oven heating functions as printed on Miele oven panels.

Checked on a H6860BP: Heißluft plus, Grill groß and Grill klein (the
translation said Lüfter plus, Vollgrill and Economy-Grill).
"""
import json
from pathlib import Path

import pytest

DE = Path(__file__).parents[1] / "custom_components/miele_lan/translations/de.json"


@pytest.mark.parametrize("sensor", ["program_id", "program_id_raw"])
@pytest.mark.parametrize(
    ("key", "name"),
    [("fan_plus", "Heißluft plus"), ("full_grill", "Grill groß"),
     ("economy_grill", "Grill klein"), ("combined_cooking", "Bräunungsgaren"),
     ("moisture_plus", "Klimagaren"), ("cake_special", "Kuchen spezial"),
     ("moisture_plus_fan_plus", "Klimagaren + Heißluft plus"),
     ("combi_cooking", "Kombigaren")],
)
def test_german_oven_function_names(sensor, key, name):
    states = json.loads(DE.read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states[key] == name
