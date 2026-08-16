"""
Build a region pack: the eBird data a station needs, cut down to one box.

  EBIRD_API_KEY=<key> uv run build-pack \
      --id iberian-peninsula \
      --name-en "Iberian Peninsula" --name-es "Peninsula iberica" \
      --bbox -10.0 35.0 4.5 44.5 \
      --output-dir /tmp/packs

A pack is a box, not a country, and it carries only what changes from one part of the world to
another. The rasters eBird publishes cover the whole globe at 9km, which is far more than any
station will ever sample: cropping them to the box is what takes a pack from gigabytes to
something a Pi can download. Values inside the box are untouched, so a station's seasonality
timeline reads a cropped raster exactly as it reads a whole one.

A pack carries no species list. The species derived here decide which data to fetch and nothing
else, because a list is only right for the point it was made for, and a box-wide one would make
the rare-species rule meaningless. Every station builds its own from its coordinates.

Downloads land in the working directory (see regional_packs/__init__.py), so a second pack over
neighbouring ground re-fetches nothing. --skip-download builds from whatever is already there,
which is what to use while working on the crop or the maps.

Publishing is not this script's job. It writes a file and prints where it went. With --index it
also merges the pack's entry into a packs index, which is what a station reads to find the box it
falls in.

Output is key=value lines on stdout and progress on stderr, so a caller can eval it.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any
from typing import NoReturn

from backyardchirps.features.species.entity import Species
from backyardchirps.features.species.maintenance import geomodel_is_available
from django.conf import settings

from regional_packs import WORK_DIR
from regional_packs.box import BoundingBox
from regional_packs.ebird import BAND_DATES_FILE
from regional_packs.ebird import OCCURRENCE_PRODUCT
from regional_packs.ebird import RANGE_PRODUCT
from regional_packs.ebird import EbirdDownloader
from regional_packs.ebird import species_over
from regional_packs.index import update_index
from regional_packs.range_maps import render_range_maps
from regional_packs.rasters import crop_raster

PACK_ID = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PACK_VERSION = re.compile(r"^[A-Za-z0-9.-]+$")

# How far apart the points GeoModel is asked about are, in degrees. The species living in a box
# are not the species living at its centre, and a coastal bird present in a narrow strip is the
# case that gets missed, so this is deliberately finer than the ground a single station covers.
# Every point costs 48 model runs, which are milliseconds each.
DEFAULT_GRID_STEP = 0.5


def main() -> None:
    arguments = _parse_arguments()

    if not PACK_ID.match(arguments.id):
        _fail(f"A pack id is lowercase words joined by dashes, for example iberian-peninsula. Got '{arguments.id}'.")
    # Both of these end up in a file name and in a directory a station unpacks into, so neither
    # is allowed to carry a path separator.
    if not PACK_VERSION.match(arguments.version):
        _fail(f"A pack version is letters, digits, dots and dashes, for example 2026-08-16. Got '{arguments.version}'.")
    if arguments.index and not arguments.base_url:
        _fail("--index needs --base-url, since an index entry has to say where the pack can be downloaded from.")

    try:
        box = BoundingBox(*arguments.bbox)
        points = box.grid_points(arguments.grid_step)
    except ValueError as error:
        _fail(str(error))

    if not geomodel_is_available():
        _fail(f"GeoModel is not in {WORK_DIR}. Run: uv run download-models")

    _say(f"{len(points)} grid points over the box, {arguments.grid_step} degrees apart")
    species = species_over(points)

    source_dir = Path(settings.EBIRD_DATA_DIR)
    if arguments.skip_download:
        _say(f"skipping the download, building from what is already in {source_dir}")
    else:
        _download(species, source_dir, arguments.skip_maps)

    output_dir = arguments.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    described = {
        "id": arguments.id,
        "names": {"en": arguments.name_en, "es": arguments.name_es},
        "bbox": box.as_dict(),
        "version": arguments.version,
    }
    with tempfile.TemporaryDirectory() as staging_parent:
        staging = Path(staging_parent) / arguments.id
        pack = _stage(staging, described, species, source_dir, box, arguments.skip_maps)
        tarball_path = _write_archive(Path(staging_parent), arguments.id, arguments.version, output_dir)

    with tarball_path.open("rb") as archive:
        checksum = hashlib.file_digest(archive, "sha256").hexdigest()
    size_bytes = tarball_path.stat().st_size

    if arguments.index:
        update_index(arguments.index, arguments.base_url, pack, tarball_path.name, checksum, size_bytes)
        _say(f"updated {arguments.index}")

    _say(f"wrote {tarball_path}")
    print(f"PACK_ID={arguments.id}")
    print(f"PACK_VERSION={arguments.version}")
    print(f"PACK_PATH={tarball_path}")
    print(f"PACK_SPECIES={pack['species_count']}")
    print(f"SHA256={checksum}")
    print(f"SIZE_BYTES={size_bytes}")


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a region pack for a bounding box.")
    parser.add_argument("--id", required=True, help="pack id, lowercase words joined by dashes")
    parser.add_argument("--name-en", required=True, help="the region's name in English")
    parser.add_argument("--name-es", required=True, help="the region's name in Spanish")
    # Four values rather than one comma-separated string, because argparse reads a lone
    # negative number as a value and a string starting with one as an option, and every box
    # west of Greenwich starts with a minus.
    parser.add_argument(
        "--bbox",
        nargs=4,
        type=float,
        required=True,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="the box in degrees, as west south east north",
    )
    parser.add_argument("--output-dir", type=Path, default=Path.cwd(), help="where to write the pack")
    parser.add_argument(
        "--grid-step",
        type=float,
        default=DEFAULT_GRID_STEP,
        help=f"how far apart to ask GeoModel about the box, in degrees (default: {DEFAULT_GRID_STEP})",
    )
    parser.add_argument(
        "--version",
        default=datetime.now(timezone.utc).date().isoformat(),
        help="the pack version, a date by default, so that a later pack sorts above an earlier one",
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="build from what is already downloaded, without asking eBird for anything",
    )
    parser.add_argument(
        "--skip-maps",
        action="store_true",
        help="leave range_maps empty, which is much faster and costs a station only the map on a species page",
    )
    parser.add_argument("--index", type=Path, help="a packs index to merge this pack's entry into")
    parser.add_argument("--base-url", help="where packs are published, used to build the index entry's url")
    return parser.parse_args()


def _download(species: list[Species], source_dir: Path, skip_maps: bool) -> None:
    access_key = os.environ.get("EBIRD_API_KEY")
    if not access_key:
        _fail("Set EBIRD_API_KEY to your eBird Status & Trends access key, or pass --skip-download.")

    products = [OCCURRENCE_PRODUCT] if skip_maps else [OCCURRENCE_PRODUCT, RANGE_PRODUCT]
    _say(f"downloading {', '.join(products)} into {source_dir}")
    downloader = EbirdDownloader(access_key)
    for one_species in species:
        for product in products:
            downloader.download_species(str(one_species.ebird_code()), source_dir, product)


def _stage(
    staging: Path,
    described: dict[str, Any],
    species: list[Species],
    source_dir: Path,
    box: BoundingBox,
    skip_maps: bool,
) -> dict[str, Any]:
    """
    Lay the pack out under a temporary directory, exactly as it will be unpacked into
    packs/<id>, and return the pack.json it wrote.
    """
    _say("cropping")
    occurrence_dir = staging / "ebird_occurrence"
    occurrence_dir.mkdir(parents=True)

    cropped = 0
    not_downloaded = []
    outside_the_box = []
    for one_species in species:
        code = str(one_species.ebird_code())
        source_path = _source_raster(source_dir / code)
        if source_path is None:
            not_downloaded.append(code)
            continue
        if not crop_raster(source_path, occurrence_dir / code / source_path.name, box):
            outside_the_box.append(code)
            continue
        shutil.copy2(source_dir / code / BAND_DATES_FILE, occurrence_dir / code / BAND_DATES_FILE)
        cropped += 1

    if not_downloaded:
        _say(f"no raster on disk for {len(not_downloaded)} species: {', '.join(sorted(not_downloaded))}")
    if outside_the_box:
        _say(f"raster does not reach the box for {len(outside_the_box)} species: {', '.join(sorted(outside_the_box))}")
    _say(f"{cropped} species cropped into the pack")

    maps_dir = staging / "range_maps"
    if skip_maps:
        # Created anyway, so the pack has the shape a station expects. A station shows a map
        # only when the file is there, so an empty directory costs it the map and nothing else.
        maps_dir.mkdir()
    else:
        drawn = render_range_maps(
            species,
            source_dir,
            maps_dir,
            Path(settings.SPECIES_RUNTIME_DIR) / "range_map_cache",
            box,
            report=_say,
        )
        _say(f"{drawn} range maps in the pack")

    pack = {**described, "species_count": cropped}
    (staging / "pack.json").write_text(json.dumps(pack, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack


def _source_raster(species_dir: Path) -> Path | None:
    if not (species_dir / BAND_DATES_FILE).is_file():
        return None
    rasters = sorted(species_dir.glob(f"*{OCCURRENCE_PRODUCT}*.tif"))
    return rasters[0] if rasters else None


def _write_archive(staging_parent: Path, pack_id: str, version: str, output_dir: Path) -> Path:
    """
    Through tar rather than through Python, which gets zstd in 3.14 and this has to run on 3.13.

    COPYFILE_DISABLE stops the tar on macOS writing a ._name AppleDouble file next to every
    entry to carry its extended attributes.
    """
    tarball_path = output_dir / f"{pack_id}-{version}.tar.zst"
    result = subprocess.run(
        ["tar", "--zstd", "-cf", str(tarball_path), "-C", str(staging_parent), pack_id],
        env={**os.environ, "COPYFILE_DISABLE": "1"},
        stdout=sys.stderr,
        check=False,
    )
    if result.returncode != 0:
        _fail(f"tar failed with exit {result.returncode}. The reason is above.")
    return tarball_path


def _say(message: str) -> None:
    print(f"[pack] {message}", file=sys.stderr)


def _fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
