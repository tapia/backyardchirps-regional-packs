"""
The parts of the region-pack builder that do not need eBird or GeoModel: reading a box,
covering it with grid points, cropping a raster to it, and merging a pack into the index.

The crops here run against rasters written in the test, in the same projection and at the same
9km pixel size as the ones eBird publishes. That is enough to check what the crop promises: the
box ends up inside the window, and the values inside it are the values that were there before.
"""

import itertools
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
from backyardchirps.features.species.entity import Species
from backyardchirps.features.species.seasonality import SeasonalityPredictor
from pyproj import Transformer
from rasterio.transform import Affine

from regional_packs import cli
from regional_packs.box import BoundingBox
from regional_packs.index import update_index
from regional_packs.rasters import crop_raster

# The projection eBird publishes Status & Trends rasters in, and the pixel size of the 9km
# product. A test raster in any other projection would not exercise the densified transform.
EQUAL_EARTH = "EPSG:8857"
PIXEL_METRES = 9000.0

IBERIA = (-10.0, 35.0, 4.5, 44.5)

# Any species the taxonomy knows will do. Its eBird code names the directory and starts the
# raster file, which is how the downloader leaves things and how SeasonalityPredictor finds them.
SPECIES = Species("Hirundo rustica")
SPECIES_CODE = str(SPECIES.ebird_code())


@pytest.fixture(scope="module")
def world_raster(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    A whole-world raster the size and shape of the ones eBird publishes: Equal Earth, 9km
    pixels, float32 with nan for nodata. Every pixel holds its own column index, so a crop can
    be checked against the pixels it came from.

    Written once for the whole module, because nothing here modifies it and the real dimensions
    are what make the window arithmetic worth testing. LZW rather than the deflate eBird uses,
    so that "the crop keeps the source's compression" is a claim about copying rather than about
    a default.
    """
    width, height = 3828, 1854
    transform = Affine(PIXEL_METRES, 0.0, -17226000.0, 0.0, -PIXEL_METRES, 8343000.0)
    band = np.tile(np.arange(width, dtype=np.float32), (height, 1))
    path = tmp_path_factory.mktemp("rasters") / "world.tif"
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=width,
        height=height,
        count=4,
        dtype="float32",
        nodata=np.nan,
        crs=EQUAL_EARTH,
        transform=transform,
        compress="lzw",
    ) as raster:
        for band_index in range(1, 5):
            raster.write(band * band_index, band_index)
    return path


@pytest.fixture(scope="module")
def whole_rasters(world_raster: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """
    The raster above laid out the way eBird's downloader leaves it: one directory per species,
    named by eBird code, holding the raster and the dates of its weekly bands. This is what a
    pack is cropped from, and what SeasonalityPredictor reads.
    """
    root = tmp_path_factory.mktemp("whole")
    species_dir = root / SPECIES_CODE
    species_dir.mkdir()
    shutil.copy2(world_raster, species_dir / f"{SPECIES_CODE}_occurrence_median_9km_2023.tif")
    dates = "band,date\n" + "".join(f"{band},2023-0{band}-04\n" for band in range(1, 5))
    (species_dir / "band-dates.csv").write_text(dates)
    return root


class TestBoundingBox:
    def test_takes_the_corners_in_west_south_east_north_order(self) -> None:
        box = BoundingBox(*IBERIA)
        assert (box.west, box.south, box.east, box.north) == (-10.0, 35.0, 4.5, 44.5)

    def test_refuses_a_box_that_crosses_the_antimeridian(self) -> None:
        # Wrapping this silently would crop every raster to the wrong half of the world.
        with pytest.raises(ValueError, match="antimeridian"):
            BoundingBox(170.0, 35.0, -170.0, 44.5)

    def test_refuses_an_upside_down_box(self) -> None:
        with pytest.raises(ValueError, match="South has to be less than north"):
            BoundingBox(-10.0, 44.5, 4.5, 35.0)

    def test_refuses_coordinates_off_the_globe(self) -> None:
        with pytest.raises(ValueError, match="Latitudes"):
            BoundingBox(-10.0, 35.0, 4.5, 91.0)


class TestGridPoints:
    def test_covers_the_corners(self) -> None:
        box = BoundingBox(*IBERIA)
        points = box.grid_points(0.5)
        assert (35.0, -10.0) in points
        assert (44.5, -10.0) in points
        assert (35.0, 4.5) in points
        assert (44.5, 4.5) in points

    def test_never_asks_about_ground_outside_the_box(self) -> None:
        box = BoundingBox(*IBERIA)
        for latitude, longitude in box.grid_points(0.7):
            assert box.south <= latitude <= box.north
            assert box.west <= longitude <= box.east

    def test_spacing_never_exceeds_the_step(self) -> None:
        # A step that does not divide the box evenly is the case worth checking: the points are
        # spread rather than laid from one edge, so the leftover never lands in one gap.
        latitudes = sorted({latitude for latitude, _ in BoundingBox(*IBERIA).grid_points(0.7)})
        gaps = [second - first for first, second in itertools.pairwise(latitudes)]
        assert max(gaps) <= 0.7 + 1e-9

    def test_a_finer_step_asks_about_more_points(self) -> None:
        box = BoundingBox(*IBERIA)
        assert len(box.grid_points(0.25)) > len(box.grid_points(1.0))

    def test_a_step_wider_than_the_box_still_covers_its_corners(self) -> None:
        box = BoundingBox(-1.0, 40.0, 1.0, 41.0)
        assert set(box.grid_points(90.0)) == {(40.0, -1.0), (40.0, 1.0), (41.0, -1.0), (41.0, 1.0)}

    def test_refuses_a_step_of_zero(self) -> None:
        with pytest.raises(ValueError, match="positive"):
            BoundingBox(*IBERIA).grid_points(0.0)


class TestCropRaster:
    def test_keeps_the_box_inside_the_crop(self, world_raster: Path, tmp_path: Path) -> None:
        box = BoundingBox(*IBERIA)
        cropped = tmp_path / "cropped.tif"

        assert crop_raster(world_raster, cropped, box) is True

        with rasterio.open(cropped) as raster:
            corners = [
                (box.west, box.south),
                (box.west, box.north),
                (box.east, box.south),
                (box.east, box.north),
                # The middle of the north edge, which is the point a box bounded by its corners
                # alone would cut off: the projection curves the edge away from them.
                ((box.west + box.east) / 2, box.north),
            ]
            for longitude, latitude in corners:
                row, column = raster.index(*_to_equal_earth(longitude, latitude))
                assert 0 <= row < raster.height
                assert 0 <= column < raster.width

    def test_values_inside_the_box_are_untouched(self, world_raster: Path, tmp_path: Path) -> None:
        box = BoundingBox(*IBERIA)
        cropped = tmp_path / "cropped.tif"
        crop_raster(world_raster, cropped, box)

        point = _to_equal_earth((box.west + box.east) / 2, (box.south + box.north) / 2)
        with rasterio.open(world_raster) as whole, rasterio.open(cropped) as part:
            assert next(whole.sample([point])).tolist() == next(part.sample([point])).tolist()

    def test_keeps_every_band_and_the_projection(self, world_raster: Path, tmp_path: Path) -> None:
        cropped = tmp_path / "cropped.tif"
        crop_raster(world_raster, cropped, BoundingBox(*IBERIA))

        with rasterio.open(world_raster) as whole, rasterio.open(cropped) as part:
            assert part.count == whole.count
            assert part.dtypes == whole.dtypes
            assert part.crs == whole.crs
            assert part.res == whole.res
            assert np.isnan(part.nodata)
            assert part.profile["compress"] == whole.profile["compress"]

    def test_the_crop_is_a_small_part_of_the_whole(self, world_raster: Path, tmp_path: Path) -> None:
        cropped = tmp_path / "cropped.tif"
        crop_raster(world_raster, cropped, BoundingBox(*IBERIA))

        with rasterio.open(world_raster) as whole, rasterio.open(cropped) as part:
            assert part.width * part.height < whole.width * whole.height / 100

    def test_reports_a_box_the_raster_does_not_reach(self, tmp_path: Path) -> None:
        # eBird ships whole-world rasters, so this should never happen. It is checked because
        # the alternative is writing an empty raster and shipping it.
        transform = Affine(PIXEL_METRES, 0.0, 0.0, 0.0, -PIXEL_METRES, 100000.0)
        source = tmp_path / "tiny.tif"
        with rasterio.open(
            source,
            "w",
            driver="GTiff",
            width=10,
            height=10,
            count=1,
            dtype="float32",
            crs=EQUAL_EARTH,
            transform=transform,
        ) as raster:
            raster.write(np.zeros((10, 10), dtype=np.float32), 1)

        assert crop_raster(source, tmp_path / "out.tif", BoundingBox(*IBERIA)) is False
        assert not (tmp_path / "out.tif").exists()


class TestStagedPack:
    """
    What the pack promises the station: seasonality.py reads a staged pack without knowing it
    was cropped, and answers what the whole raster would have answered.
    """

    def test_the_timeline_is_the_same_as_from_the_whole_raster(self, whole_rasters: Path, tmp_path: Path) -> None:
        box = BoundingBox(*IBERIA)
        staged = _stage_pack(tmp_path, whole_rasters, box)

        whole = SeasonalityPredictor(root=whole_rasters)
        part = SeasonalityPredictor(root=staged / "ebird_occurrence")
        # The corners as well as the middle: a crop that lost an edge would still agree here if
        # only the centre were checked.
        for longitude, latitude in [
            ((box.west + box.east) / 2, (box.south + box.north) / 2),
            (box.west, box.south),
            (box.east, box.north),
        ]:
            assert part.get_seasonality_timeline(SPECIES_CODE, latitude, longitude) == whole.get_seasonality_timeline(
                SPECIES_CODE, latitude, longitude
            )
        assert part.get_band_dates(SPECIES_CODE) == whole.get_band_dates(SPECIES_CODE)

    def test_carries_the_band_dates_beside_every_raster(self, whole_rasters: Path, tmp_path: Path) -> None:
        # Without them the timeline has values it cannot place in the year.
        staged = _stage_pack(tmp_path, whole_rasters, BoundingBox(*IBERIA))
        assert (staged / "ebird_occurrence" / SPECIES_CODE / "band-dates.csv").is_file()

    def test_carries_every_directory_a_station_links_to_and_a_pack_file(
        self, whole_rasters: Path, tmp_path: Path
    ) -> None:
        """
        Skipped or not, both directories are created. A station links to each of them when it
        installs a pack, and finds them empty rather than missing.
        """
        staged = _stage_pack(tmp_path, whole_rasters, BoundingBox(*IBERIA))
        assert (staged / "range_maps").is_dir()
        assert (staged / "reference_calls").is_dir()
        assert json.loads((staged / "pack.json").read_text())["species_count"] == 1


class TestPackIndex:
    def test_writes_an_entry_a_station_can_download_from(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.json"
        update_index(index_path, "https://example.com/packs/", _pack("iberian-peninsula"), "p.tar.zst", "ab", 12)

        entry = json.loads(index_path.read_text())["packs"][0]
        assert entry["url"] == "https://example.com/packs/p.tar.zst"
        assert entry["sha256"] == "ab"
        assert entry["size_bytes"] == 12
        assert entry["bbox"]["west"] == -10.0
        assert entry["species_count"] == 3

    def test_replaces_the_entry_for_a_pack_it_already_lists(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.json"
        update_index(index_path, "https://example.com", _pack("canary-islands"), "old.tar.zst", "ab", 1)
        update_index(index_path, "https://example.com", _pack("canary-islands", "2027-01-01"), "new.tar.zst", "cd", 2)

        packs = json.loads(index_path.read_text())["packs"]
        assert len(packs) == 1
        assert packs[0]["version"] == "2027-01-01"
        assert packs[0]["url"].endswith("new.tar.zst")

    def test_keeps_the_packs_it_already_lists(self, tmp_path: Path) -> None:
        index_path = tmp_path / "index.json"
        update_index(index_path, "https://example.com", _pack("iberian-peninsula"), "a.tar.zst", "ab", 1)
        update_index(index_path, "https://example.com", _pack("canary-islands"), "b.tar.zst", "cd", 1)

        packs = json.loads(index_path.read_text())["packs"]
        assert [pack["id"] for pack in packs] == ["canary-islands", "iberian-peninsula"]


def _stage_pack(tmp_path: Path, whole_rasters: Path, box: BoundingBox) -> Path:
    staged = tmp_path / "pack"
    described = {"id": "a-region", "names": {"en": "A region", "es": "Una region"}, "bbox": box.as_dict()}
    # Both skipped, because drawing a map would fetch basemap tiles and looking up a reference
    # call would ask xeno-canto. Neither is what these tests are about.
    cli._stage(staged, described, [SPECIES], whole_rasters, box, skip_maps=True, skip_calls=True)
    return staged


def _pack(pack_id: str, version: str = "2026-08-16") -> dict:
    return {
        "id": pack_id,
        "names": {"en": "A region", "es": "Una region"},
        "bbox": {"west": -10.0, "south": 35.0, "east": 4.5, "north": 44.5},
        "version": version,
        "species_count": 3,
    }


def _to_equal_earth(longitude: float, latitude: float) -> tuple[float, float]:
    transformer = Transformer.from_crs("EPSG:4326", EQUAL_EARTH, always_xy=True)
    return transformer.transform(longitude, latitude)
