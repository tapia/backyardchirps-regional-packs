"""
Drawing one range map per species, framed on the pack's box.

Each eBird range GeoPackage holds seasonal polygons for one species. They are collapsed into four
categories, laid over a shaded basemap of the box, and written as WebP named by the species slug,
which is the name `Species.map_url` looks for.

The basemap is fetched once and reused for every species in the pack, so the cost of the tiles is
paid once however many birds live in the box.
"""

import math
from pathlib import Path

import contextily
import geopandas
import numpy as np
import pyproj
import shapely
from backyardchirps.features.species.entity import Species
from PIL import Image
from rasterio.features import rasterize
from rasterio.transform import from_bounds
from shapely.ops import transform as shapely_transform

from regional_packs.box import BoundingBox

GPKG_LAYER = "range"
SOURCE_CRS = "EPSG:4326"
MAP_CRS = "EPSG:3857"

# Label-free tiles, so the map reads as a range map rather than as a road atlas, darkened by the
# luminance of a relief layer so mountain ranges show. Both come from the same bounds and zoom,
# which is what makes them share a pixel grid.
BASEMAP_SOURCE = contextily.providers.CartoDB.PositronNoLabels
RELIEF_SOURCE = contextily.providers.Esri.WorldShadedRelief
RELIEF_STRENGTH = 0.75

# The longest side of the finished image. The basemap keeps its own proportions inside it, so a
# wide box comes out wide instead of being squeezed into a square.
MAX_SIDE_PIXELS = 2400
TILE_PIXELS = 256

# Opacity of the multiply blend. Multiply keeps the terrain visible through the fill.
ALPHA = 0.6

# Polygons are burned at this multiple of the output resolution and averaged back down, which is
# what anti-aliases their edges.
SUPERSAMPLE = 3

WEBP_QUALITY = 90
# The slowest and smallest WebP setting. A pack is built once and downloaded many times.
WEBP_METHOD = 6

# Which raw season values collapse into each drawn category.
CATEGORY_SEASONS: dict[str, set[str]] = {
    "resident": {"resident"},
    "breeding": {"breeding"},
    "migration": {"prebreeding_migration", "postbreeding_migration"},
    "nonbreeding": {"nonbreeding"},
}

# Highest first. Each category is subtracted from every lower one, so no two drawn layers ever
# cover the same ground and colours never blend into a fifth meaning.
PRIORITY = ["resident", "breeding", "nonbreeding", "migration"]

COLORS = {
    "resident": "#33a02c",
    "breeding": "#d95f02",
    "nonbreeding": "#1f78b4",
    "migration": "#f1c40f",
}


def render_range_maps(species: list[Species], source_dir: Path, destination_dir: Path, box: BoundingBox) -> int:
    """
    Draw a map for every species with a range GeoPackage on disk, and return how many were
    written. A species without one is skipped: the pack loses that map and nothing else.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    basemap, extent = build_basemap(box)
    height, width = basemap.shape[:2]
    left, right, bottom, top = extent
    transform = from_bounds(left, bottom, right, top, width * SUPERSAMPLE, height * SUPERSAMPLE)
    to_mercator = mercator_transformer()

    drawn = 0
    for one_species in species:
        source_path = _range_package(source_dir / str(one_species.ebird_code()))
        if source_path is None:
            continue
        _render_one(source_path, basemap, transform, to_mercator, destination_dir / f"{one_species.slug}.webp")
        drawn += 1
    return drawn


def build_basemap(box: BoundingBox) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """
    Download the basemap and the shaded relief for the box, blend them and scale the result to
    the output size. Returns float RGB in [0, 1] and the Web Mercator extent it covers, which is
    the extent of the tiles fetched rather than the box itself.

    The zoom is worked out once and passed to both layers. Letting each choose its own would
    give two different tile grids for the same ground, and the blend would be nonsense.
    """
    zoom = _zoom_for(box)
    basemap, extent = contextily.bounds2img(
        box.west, box.south, box.east, box.north, zoom=zoom, source=BASEMAP_SOURCE, ll=True
    )
    relief, _ = contextily.bounds2img(
        box.west, box.south, box.east, box.north, zoom=zoom, source=RELIEF_SOURCE, ll=True
    )

    base_rgb = basemap[..., :3].astype(np.float64) / 255.0
    relief_luminance = relief[..., :3].astype(np.float64).mean(axis=2, keepdims=True) / 255.0
    shadow = 1.0 - RELIEF_STRENGTH * (1.0 - relief_luminance)
    blended = np.clip(base_rgb * shadow, 0.0, 1.0)

    tile_height, tile_width = blended.shape[:2]
    scale = MAX_SIDE_PIXELS / max(tile_height, tile_width)
    scaled = Image.fromarray((blended * 255.0).astype(np.uint8)).resize(
        (round(tile_width * scale), round(tile_height * scale)), Image.Resampling.BILINEAR
    )
    return np.asarray(scaled, dtype=np.float64) / 255.0, extent


def priority_layers(ranges: geopandas.GeoDataFrame) -> dict[str, shapely.Geometry]:
    """
    One geometry per category, with overlaps subtracted so that no two cover the same ground.

    Ground that is both breeding and non-breeding is promoted to resident, which is eBird's own
    convention and what stops a partial migrant being drawn as a summer bird.
    """
    unions: dict[str, shapely.Geometry] = {}
    for category, seasons in CATEGORY_SEASONS.items():
        geometries = ranges.loc[ranges["season"].isin(seasons), "geometry"].values
        if len(geometries):
            unions[category] = shapely.union_all(geometries)

    breeding = unions.get("breeding")
    nonbreeding = unions.get("nonbreeding")
    if breeding is not None and nonbreeding is not None:
        year_round = breeding.intersection(nonbreeding)
        if not year_round.is_empty:
            already = unions.get("resident")
            unions["resident"] = year_round if already is None else shapely.union_all([already, year_round])

    layers: dict[str, shapely.Geometry] = {}
    covered: shapely.Geometry | None = None
    for category in PRIORITY:
        geometry = unions.get(category)
        if geometry is None or geometry.is_empty:
            continue
        drawn = geometry if covered is None else geometry.difference(covered)
        if not drawn.is_empty:
            layers[category] = drawn
        covered = geometry if covered is None else shapely.union_all([covered, geometry])
    return layers


def _zoom_for(box: BoundingBox) -> int:
    """
    A zoom level whose tiles roughly match the output resolution, so the map is neither a blur
    nor thousands of tiles. One tile spans 360 / 2**zoom degrees of longitude.
    """
    tiles_across = MAX_SIDE_PIXELS / TILE_PIXELS
    span = max(box.east - box.west, box.north - box.south)
    return max(1, min(12, math.ceil(math.log2(360.0 * tiles_across / span))))


def mercator_transformer() -> pyproj.Transformer:
    return pyproj.Transformer.from_crs(SOURCE_CRS, MAP_CRS, always_xy=True)


def _range_package(species_dir: Path) -> Path | None:
    packages = sorted(species_dir.glob("*range_smooth_9km*.gpkg"))
    return packages[0] if packages else None


def _render_one(
    source_path: Path,
    basemap: np.ndarray,
    transform: object,
    to_mercator: pyproj.Transformer,
    destination_path: Path,
) -> None:
    ranges = geopandas.read_file(source_path, layer=GPKG_LAYER)
    layers = {
        category: shapely_transform(to_mercator.transform, geometry)
        for category, geometry in priority_layers(ranges).items()
    }
    coverage = _coverage_fractions(layers, transform, basemap.shape[:2])

    # Photoshop "multiply": out = base * colour, mixed in by opacity times coverage. The layers
    # are disjoint, so applying them one after another is order-independent.
    composite = basemap.copy()
    for category, fraction in coverage.items():
        weight = (ALPHA * fraction)[..., np.newaxis]
        composite = composite * (1.0 - weight) + (composite * _rgb(COLORS[category])) * weight

    pixels = (np.clip(composite, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    Image.fromarray(pixels, mode="RGB").save(destination_path, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)


def _coverage_fractions(
    layers: dict[str, shapely.Geometry],
    transform: object,
    shape: tuple[int, int],
) -> dict[str, np.ndarray]:
    """
    How much of each output pixel each layer covers, from 0 to 1. The layers are disjoint, so one
    pass burning them into a single label raster is enough.
    """
    ordered = [category for category in PRIORITY if category in layers]
    if not ordered:
        return {}

    height, width = shape
    labels = rasterize(
        [(layers[category], value) for value, category in enumerate(ordered, 1)],
        out_shape=(height * SUPERSAMPLE, width * SUPERSAMPLE),
        transform=transform,
        fill=0,
        dtype=np.uint8,
    )
    blocks = labels.reshape(height, SUPERSAMPLE, width, SUPERSAMPLE)
    return {category: (blocks == value).mean(axis=(1, 3)) for value, category in enumerate(ordered, 1)}


def _rgb(hex_color: str) -> np.ndarray:
    digits = hex_color.lstrip("#")
    return np.array([int(digits[index : index + 2], 16) / 255.0 for index in (0, 2, 4)])
