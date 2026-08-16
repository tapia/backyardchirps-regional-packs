"""
Everything that talks to eBird Status & Trends, and the species derivation that decides what to
ask it for.
"""

from pathlib import Path
from typing import Any

import requests
from backyardchirps.features.species.entity import Species
from backyardchirps.features.species.maintenance import plausible_species_names_over

# The two products a pack is made of. The occurrence raster is what a station samples for its
# seasonality timeline, and seasonality.py finds it by globbing for this same token. The range
# GeoPackage holds the seasonal polygons the range maps are drawn from.
OCCURRENCE_PRODUCT = "occurrence_median_9km"
RANGE_PRODUCT = "range_smooth_9km"

# eBird ships the calendar date of each weekly band beside the raster, and a timeline cannot
# place a value in the year without it.
BAND_DATES_FILE = "band-dates.csv"


class EbirdDownloader:
    """
    Fetches published products for one species at a time, skipping whatever is already on disk.
    """

    BASE = "https://st-download.ebird.org/v1"

    def __init__(self, access_key: str, version: int = 2023):
        self.access_key = access_key
        self.version = version

    def download_species(self, species_code: str, output_dir: Path, product: str) -> None:
        species_dir = output_dir / species_code
        species_dir.mkdir(parents=True, exist_ok=True)

        wanted = [obj for obj in self._list_objects(species_code) if self._is_wanted(obj, product)]

        for obj in wanted:
            filename = species_dir / Path(obj).name
            if filename.exists():
                continue

            url = f"{self.BASE}/fetch?objKey={obj}&key={self.access_key}"
            print("Downloading", filename.name)

            with requests.get(url, stream=True) as response:
                response.raise_for_status()
                with open(filename, "wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        handle.write(chunk)

    def _is_wanted(self, obj: str, product: str) -> bool:
        if product in obj:
            return True
        # Occurrence rasters need their band-dates.csv companion for the timeline.
        return product.startswith("occurrence") and obj.endswith("band-dates.csv")

    def _list_objects(self, species_code: str) -> Any:
        response = requests.get(f"{self.BASE}/list-obj/{self.version}/{species_code}?key={self.access_key}")
        response.raise_for_status()
        return response.json()


def species_over(points: list[tuple[float, float]]) -> list[Species]:
    """
    Every species plausible at any of these points that eBird has a code for, sorted.

    Both halves are derived rather than stored: the species come from GeoModel through the same
    call a station uses to build its own list, so the two can never disagree about what counts as
    plausible; the codes come from the taxonomy. A species the taxonomy has no code for is
    skipped, since there is no data to ask eBird for.
    """
    scientific_names = plausible_species_names_over(points)

    with_code = []
    without_code = []
    for scientific_name in scientific_names:
        species = Species(scientific_name)
        if species.ebird_code():
            with_code.append(species)
        else:
            without_code.append(scientific_name)

    print(f"{len(scientific_names)} species plausible, {len(with_code)} with an eBird code")
    if without_code:
        print(f"No eBird code, skipped: {', '.join(without_code)}")
    return with_code
