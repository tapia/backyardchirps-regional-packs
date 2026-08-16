"""
Cutting a whole-world occurrence raster down to the ground a pack covers.
"""

import math
from pathlib import Path

import rasterio
from rasterio.warp import transform_bounds
from rasterio.windows import Window
from rasterio.windows import from_bounds

from regional_packs.box import BoundingBox


def crop_raster(source_path: Path, destination_path: Path, box: BoundingBox) -> bool:
    """
    Copy the part of a raster that falls inside the box, keeping every band, the projection,
    the pixel grid and the data type. Values are unchanged, so a station sampling a point in
    the box reads what it would have read from the whole raster.

    Returns False when the box and the raster do not overlap at all, which means there is
    nothing to ship for that species.
    """
    with rasterio.open(source_path) as source:
        window = _window_over(source, box)
        if window is None:
            return False

        profile = source.profile.copy()
        profile.update(
            width=int(window.width),
            height=int(window.height),
            transform=source.window_transform(window),
        )
        # The source's block layout is dropped rather than copied. A crop is a few hundred
        # pixels across, so the tiles a global raster is cut into are larger than the whole
        # file, and GDAL rejects the combination outright the moment a source turns out not to
        # be tiled. Everything that decides what the numbers are, meaning the projection, the
        # pixel size, the data type, the nodata value and the compression, is kept. So is the
        # interleaving, since a station reads one pixel across every week at once.
        for blocking in ("blockxsize", "blockysize", "tiled"):
            profile.pop(blocking, None)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(destination_path, "w", **profile) as destination:
            destination.write(source.read(window=window))
    return True


def _window_over(source: rasterio.io.DatasetReader, box: BoundingBox) -> Window | None:
    """
    The block of pixels covering the box, rounded outwards so the box is inside it, and clipped
    to the raster.

    The rasters are in an equal-area projection, so the box is a curved quadrilateral once
    projected and its corners alone do not bound it. densify_pts walks the edges instead, which
    is what keeps the north edge of a wide box from being cut off.
    """
    left, bottom, right, top = transform_bounds(
        "EPSG:4326",
        source.crs,
        box.west,
        box.south,
        box.east,
        box.north,
        densify_pts=51,
    )
    wanted = from_bounds(left, bottom, right, top, transform=source.transform)

    first_column = max(0, math.floor(wanted.col_off))
    first_row = max(0, math.floor(wanted.row_off))
    last_column = min(source.width, math.ceil(wanted.col_off + wanted.width))
    last_row = min(source.height, math.ceil(wanted.row_off + wanted.height))
    if last_column <= first_column or last_row <= first_row:
        return None

    return Window(first_column, first_row, last_column - first_column, last_row - first_row)
