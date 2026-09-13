"""
Every pack in the index on one map, for the README.

The image is drawn from index.json and from nothing else, because the index is what stations
resolve their coordinates against: a box on this map is ground a station can be offered a pack
for, and nothing that is only planned or half-built shows up on it. `make pack` redraws it after
merging a pack into the index, so the two are committed together.
"""

import argparse
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.backend_bases import RendererBase
from matplotlib.patches import Rectangle
from matplotlib.text import Text
from matplotlib.transforms import Bbox
from matplotlib.transforms import IdentityTransform
from PIL import Image

from regional_packs.box import BoundingBox
from regional_packs.range_maps import build_basemap
from regional_packs.range_maps import mercator_transformer

# Wide enough to read on a README page, and a basemap fetched at that size so it stays sharp.
WIDTH_PIXELS = 1600
DPI = 160
# Higher than a range map's, since the labels are text and text shows lossy artefacts first.
WEBP_QUALITY = 92

# Ground drawn around the packs, as a fraction of their combined span. Enough for a label to
# sit outside a box too small to hold it.
MARGIN = 0.12

# How far the basemap is lifted towards white, so the boxes are what the eye lands on.
BASEMAP_WASH = 0.35

# One colour per pack, in index order, and dark enough to read as text on white.
PALETTE = ["#1565c0", "#c2185b", "#00796b", "#6a1b9a", "#e65100", "#2e7d32", "#37474f", "#ad1457"]

FILL_ALPHA = 0.10
LINE_WIDTH = 1.8
# The rounded box behind a label, as a fraction of the font size.
LABEL_PAD = 0.4
FONT_SIZE = 10.5

# Esri's terms ask for this on anything drawn from their tiles.
ATTRIBUTION = "Basemap: Esri World Gray Canvas, Esri World Shaded Relief"


@dataclass(frozen=True)
class Pack:
    id: str
    name: str
    box: BoundingBox
    species_count: int
    size_bytes: int

    @property
    def label(self) -> str:
        megabytes = self.size_bytes / 1_000_000
        size = f"{megabytes:.1f}" if megabytes < 10 else f"{megabytes:.0f}"
        return f"{self.name}\n{self.species_count} species · {size} MB"


def main() -> None:
    parser = argparse.ArgumentParser(description="Draw every pack in the index on one map.")
    parser.add_argument("--index", type=Path, default=Path("index.json"), help="the packs index to draw")
    parser.add_argument("--output", type=Path, required=True, help="where to write the WebP image")
    arguments = parser.parse_args()

    packs = read_packs(arguments.index)
    if not packs:
        print(f"{arguments.index} lists no packs, so there is nothing to draw.", file=sys.stderr)
        raise SystemExit(1)

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    draw(packs, arguments.output)
    print(f"Wrote {arguments.output} ({len(packs)} packs)", file=sys.stderr)


def read_packs(index_path: Path) -> list[Pack]:
    index: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
    return [
        Pack(
            id=entry["id"],
            name=entry["names"]["en"],
            box=BoundingBox(**entry["bbox"]),
            species_count=entry["species_count"],
            size_bytes=entry["size_bytes"],
        )
        for entry in index.get("packs", [])
    ]


def frame_around(boxes: list[BoundingBox]) -> BoundingBox:
    """
    The smallest box holding every pack. The margin is added by the basemap, which is also
    what keeps it inside the ground Mercator can draw.
    """
    return BoundingBox(
        min(box.west for box in boxes),
        min(box.south for box in boxes),
        max(box.east for box in boxes),
        max(box.north for box in boxes),
    )


def draw(packs: list[Pack], output: Path) -> None:
    basemap, extent = build_basemap(frame_around([pack.box for pack in packs]), margin=MARGIN, max_side=WIDTH_PIXELS)
    basemap = 1.0 - (1.0 - basemap) * (1.0 - BASEMAP_WASH)
    height, width = basemap.shape[:2]
    left, right, bottom, top = extent

    # The axes fill the figure and the figure has the basemap's proportions, so one pixel of
    # the basemap is one pixel of the PNG.
    figure = plt.figure(figsize=(WIDTH_PIXELS / DPI, WIDTH_PIXELS / DPI * height / width), dpi=DPI)
    axes = figure.add_axes((0.0, 0.0, 1.0, 1.0))
    axes.imshow(basemap, extent=(left, right, bottom, top), interpolation="bilinear", zorder=0)
    axes.set_xlim(left, right)
    axes.set_ylim(bottom, top)
    axes.set_axis_off()

    to_mercator = mercator_transformer()
    rectangles: list[tuple[float, float, float, float]] = []
    for pack, colour in zip(packs, _colours(len(packs)), strict=True):
        x0, y0 = to_mercator.transform(pack.box.west, pack.box.south)
        x1, y1 = to_mercator.transform(pack.box.east, pack.box.north)
        rectangles.append((x0, y0, x1, y1))
        axes.add_patch(
            Rectangle((x0, y0), x1 - x0, y1 - y0, facecolor=colour, alpha=FILL_ALPHA, edgecolor="none", zorder=2)
        )
        axes.add_patch(
            Rectangle(
                (x0, y0),
                x1 - x0,
                y1 - y0,
                facecolor="none",
                edgecolor=colour,
                linewidth=LINE_WIDTH,
                joinstyle="miter",
                zorder=3,
            )
        )

    _place_labels(axes, packs, rectangles)
    axes.text(
        0.995,
        0.005,
        ATTRIBUTION,
        transform=axes.transAxes,
        ha="right",
        va="bottom",
        fontsize=6,
        color="#52525b",
        zorder=6,
    )
    # Rendered to PNG and written as WebP, like a range map. The image is committed and redrawn
    # with every pack, so each redraw stays in the history for good: about a tenth of the PNG.
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=DPI, facecolor="white")
    plt.close(figure)
    Image.open(buffer).convert("RGB").save(output, format="WEBP", quality=WEBP_QUALITY, method=6)


def _colours(count: int) -> list[str]:
    return [PALETTE[index % len(PALETTE)] for index in range(count)]


def _place_labels(axes: Axes, packs: list[Pack], rectangles: list[tuple[float, float, float, float]]) -> None:
    """
    Put each pack's label where it hides the least.

    A label goes in a corner inside its own box if it fits there, and just outside the box if
    it does not, which is what a small island box needs. Of the places it could go, it takes the
    one that covers no other label, stays on the map and lies over the fewest other boxes, so
    overlapping packs such as France and Iberia each keep their label on their own ground.
    """
    figure = axes.get_figure()
    assert figure is not None
    figure.canvas.draw()
    renderer = figure.canvas.get_renderer()  # type: ignore[attr-defined]
    to_display = axes.transData.transform
    frame = axes.get_window_extent(renderer)
    # Clear of the outline by the label's own rounded padding and a little more, in pixels.
    pad = (LABEL_PAD * FONT_SIZE + 4.0) * DPI / 72.0

    display_boxes = [Bbox([to_display((x0, y0)), to_display((x1, y1))]) for x0, y0, x1, y1 in rectangles]
    placed: list[Bbox] = []

    for index, (pack, colour) in enumerate(zip(packs, _colours(len(packs)), strict=True)):
        own = display_boxes[index]
        others = [other for position, other in enumerate(display_boxes) if position != index]
        best: tuple[float, Text, Bbox] | None = None

        for order, (x, y, ha, va, inside) in enumerate(_candidates(own, pad)):
            text = _label(axes, pack.label, colour, x, y, ha, va)
            extent = _extent(text, renderer)
            if inside and not _contains(own, extent):
                text.remove()
                continue
            cost = (
                100.0 * sum(extent.overlaps(other) for other in placed)
                + 50.0 * (not _contains(frame, extent))
                + 5.0 * sum(extent.overlaps(other) for other in others)
                + 5.0 * (not inside and extent.overlaps(own))
                + order * 0.1
            )
            if best is None or cost < best[0]:
                if best is not None:
                    best[1].remove()
                best = (cost, text, extent)
            else:
                text.remove()

        assert best is not None, "the outside candidates are never skipped"
        placed.append(best[2])


def _candidates(box: Bbox, pad: float) -> list[tuple[float, float, str, str, bool]]:
    """
    Where a label may go, in display pixels, most wanted first: the four inside corners, then
    below, above, right of and left of the box.
    """
    return [
        (box.x0 + pad, box.y1 - pad, "left", "top", True),
        (box.x1 - pad, box.y1 - pad, "right", "top", True),
        (box.x0 + pad, box.y0 + pad, "left", "bottom", True),
        (box.x1 - pad, box.y0 + pad, "right", "bottom", True),
        (box.x0, box.y0 - pad, "left", "top", False),
        (box.x0, box.y1 + pad, "left", "bottom", False),
        (box.x1 + pad, box.y1, "left", "top", False),
        (box.x0 - pad, box.y1, "right", "top", False),
    ]


def _label(axes: Axes, content: str, colour: str, x: float, y: float, ha: str, va: str) -> Text:
    text = axes.text(
        x,
        y,
        content,
        transform=IdentityTransform(),
        ha=ha,
        va=va,
        fontsize=FONT_SIZE,
        color=colour,
        linespacing=1.4,
        zorder=5,
    )
    text.set_bbox(
        {
            "boxstyle": f"round,pad={LABEL_PAD}",
            "facecolor": "white",
            "alpha": 0.9,
            "edgecolor": colour,
            "linewidth": 0.6,
        }
    )
    return text


def _extent(text: Text, renderer: RendererBase) -> Bbox:
    """
    What the label covers, rounded box and all.
    """
    patch = text.get_bbox_patch()
    if patch is None:
        return text.get_window_extent(renderer)
    text.draw(renderer)
    return patch.get_window_extent(renderer)


def _contains(outer: Bbox, inner: Bbox) -> bool:
    return bool(outer.x0 <= inner.x0 and inner.x1 <= outer.x1 and outer.y0 <= inner.y0 and inner.y1 <= outer.y1)


if __name__ == "__main__":
    main()
