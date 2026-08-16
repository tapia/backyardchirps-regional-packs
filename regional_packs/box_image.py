"""
Draw a box on a map, so a bounding box can be looked at before anything is built from it.

A box is four numbers that end up inside pack.json and index.json, where a wrong one is expensive
to take back: stations resolve their coordinates against it, and a box that misses an island
means nobody there is ever offered a pack. Nothing else in this repository checks that a box
contains what its name says, and no amount of testing can, because the question is geographic
rather than arithmetic.

This takes seconds, needs no eBird key and downloads no species. It draws the same basemap a pack
would be drawn on, so what you see is what the range maps will be framed on.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

from regional_packs.box import BoundingBox
from regional_packs.range_maps import build_basemap
from regional_packs.range_maps import mercator_transformer

# A colour no basemap or relief uses, so the line is never mistaken for a coast.
OUTLINE = np.array([220, 20, 60], dtype=np.uint8)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a bounding box as an image, to check it covers what you meant."
    )
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="the box in degrees, as west south east north",
    )
    parser.add_argument("--output", type=Path, required=True, help="where to write the PNG")
    arguments = parser.parse_args()

    try:
        box = BoundingBox(*arguments.bbox)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None

    basemap, extent = build_basemap(box)
    marked = _mark_the_box(basemap, extent, box)

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(marked, mode="RGB").save(arguments.output)

    height, width = basemap.shape[:2]
    print(f"Wrote {arguments.output} ({width}x{height})", file=sys.stderr)
    print(
        "Ground outside the box is dimmed. Only what is inside ships, so anything you expect in "
        "the pack has to sit clearly inside, not on the line.",
        file=sys.stderr,
    )


def _mark_the_box(basemap: np.ndarray, extent: tuple[float, float, float, float], box: BoundingBox) -> np.ndarray:
    """
    Dim everything outside the box and outline it.

    The tiles reach past the box, which is the trap this whole tool exists to avoid: an island in
    the margin looks covered and is not. Dimming says which is which without anyone measuring
    pixels.
    """
    height, width = basemap.shape[:2]
    left, right, bottom, top = extent
    transformer = mercator_transformer()
    box_left, box_bottom = transformer.transform(box.west, box.south)
    box_right, box_top = transformer.transform(box.east, box.north)

    first_column = round((box_left - left) / (right - left) * width)
    last_column = round((box_right - left) / (right - left) * width)
    # Rows run the other way: the top of the image is the highest northing.
    first_row = round((top - box_top) / (top - bottom) * height)
    last_row = round((top - box_bottom) / (top - bottom) * height)

    inside = np.zeros((height, width), dtype=bool)
    inside[max(0, first_row) : last_row, max(0, first_column) : last_column] = True

    marked = np.clip(basemap, 0.0, 1.0)
    marked = np.where(inside[..., np.newaxis], marked, marked * 0.45 + 0.15)

    pixels: np.ndarray = (marked * 255.0).round().astype(np.uint8)
    for row in (first_row, last_row - 1):
        if 0 <= row < height:
            pixels[row, max(0, first_column) : last_column] = OUTLINE
    for column in (first_column, last_column - 1):
        if 0 <= column < width:
            pixels[max(0, first_row) : last_row, column] = OUTLINE
    return pixels


if __name__ == "__main__":
    main()
