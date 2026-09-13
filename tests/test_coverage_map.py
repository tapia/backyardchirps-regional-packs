"""
The parts of the coverage map that do not need tiles: reading the index and framing the packs.
"""

import json
from pathlib import Path

from regional_packs.box import BoundingBox
from regional_packs.coverage_map import frame_around
from regional_packs.coverage_map import read_packs

INDEX = Path(__file__).parent.parent / "index.json"


def test_every_pack_in_the_index_is_drawn() -> None:
    listed = {entry["id"] for entry in json.loads(INDEX.read_text(encoding="utf-8"))["packs"]}

    assert {pack.id for pack in read_packs(INDEX)} == listed


def test_the_frame_holds_every_box() -> None:
    iberia = BoundingBox(-10.8, 34.2, 5.4, 44.9)
    canaries = BoundingBox(-18.6, 27.4, -13.1, 29.8)
    france = BoundingBox(-5.2, 42.3, 8.3, 51.2)

    assert frame_around([iberia, canaries, france]) == BoundingBox(-18.6, 27.4, 8.3, 51.2)


def test_a_label_names_the_pack_and_what_it_costs(tmp_path: Path) -> None:
    index = tmp_path / "index.json"
    entry = {
        "id": "small",
        "names": {"en": "Small", "es": "Pequeño"},
        "bbox": {"west": 0.0, "south": 0.0, "east": 1.0, "north": 1.0},
        "species_count": 12,
        "size_bytes": 2_799_933,
    }
    index.write_text(json.dumps({"packs": [entry, {**entry, "id": "large", "size_bytes": 128_502_346}]}))

    small, large = read_packs(index)

    assert small.label == "Small\n12 species · 2.8 MB"
    assert large.label == "Small\n12 species · 129 MB"
