"""English names of oven heating functions as used in Miele's English manuals.

Gentle bake (Bräunungsgaren) and Cake plus (Kuchen spezial) follow the
English H6860BP and DGC 6660 manuals; the other names match the display.
"""
import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parents[1] / "custom_components/miele_lan"


@pytest.mark.parametrize("path", ["strings.json", "translations/en.json"])
@pytest.mark.parametrize("sensor", ["program_id", "program_id_raw"])
@pytest.mark.parametrize(
    ("key", "name"),
    [("fan_plus", "Fan plus"), ("conventional_heat", "Conventional heat"),
     ("combination_mode", "Combination mode"), ("steam_cooking", "Steam cooking"),
     ("sous_vide", "Sous-vide"), ("intensive_bake", "Intensive bake"),
     ("bottom_heat", "Bottom heat"), ("top_heat", "Top heat"),
     ("full_grill", "Full grill"), ("economy_grill", "Economy grill"),
     ("fan_grill", "Fan grill"), ("cake_plus", "Cake plus"),
     ("gentle_bake", "Gentle bake")],
)
def test_english_oven_function_names(path, sensor, key, name):
    states = json.loads((COMPONENT / path).read_text(encoding="utf-8"))["entity"]["sensor"][sensor]["state"]
    assert states[key] == name
