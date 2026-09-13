"""
Everything that talks to eBird Status & Trends, and the species derivation that decides what to
ask it for.
"""

from pathlib import Path

import requests
from backyardchirps.features.species.entity import Species
from backyardchirps.features.species.maintenance import plausible_species_names_over
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# The two products a pack is made of. The occurrence contains the seasonality timeline,
# and the range contains the seasonal polygons used to render the range maps.
OCCURRENCE_PRODUCT = "occurrence_median_9km"
RANGE_PRODUCT = "range_smooth_9km"

# eBird ships the calendar date of each weekly band beside the raster, and a timeline cannot
# place a value in the year without it.
BAND_DATES_FILE = "band-dates.csv"

# A pack requests eBird for every species, hundreds of times, so it requires retries.
# Wait 2, 4, 8, 16 and 32 seconds before giving up.
RETRY = Retry(total=5, backoff_factor=2, status_forcelist=(500, 502, 503, 504))

# eBird doesn't publish info about all species in every release, so we need a fallback to
# query previous releases. For now we only query 2023 and 2021 releases.
#
# The products are the same ones in the same format, with one main difference: the 9km resolution
# was called "mr" then, which is the only thing the downloader has to know.
FALLBACK_VERSION = 2021
RESOLUTION = "9km"
FALLBACK_RESOLUTION = "mr"


class EbirdDownloader:
    """
    Fetches published products for one species at a time, skipping whatever is already on disk.
    """

    BASE = "https://st-download.ebird.org/v1"

    def __init__(self, access_key: str, version: int = 2023):
        self.access_key = access_key
        self.version = version
        self.session = requests.Session()
        self.session.mount("https://", HTTPAdapter(max_retries=RETRY))
        # Each species is asked about once per product, so its listing is kept for the second.
        self._releases: dict[str, tuple[int, list[str]]] = {}

    def download_species(self, species_code: str, output_dir: Path, product: str) -> None:
        species_dir = output_dir / species_code
        species_dir.mkdir(parents=True, exist_ok=True)

        version, objects = self._release_for(species_code)
        if version == FALLBACK_VERSION:
            product = product.replace(RESOLUTION, FALLBACK_RESOLUTION)
        wanted = [obj for obj in objects if self._is_wanted(obj, product)]

        for obj in wanted:
            # A fallback file is saved under today's resolution name, since that name is how the
            # builder, the range maps and a station all find it. The year in it stays 2021.
            filename = species_dir / Path(obj).name.replace(f"_{FALLBACK_RESOLUTION}_", f"_{RESOLUTION}_")
            if filename.exists():
                continue

            url = f"{self.BASE}/fetch?objKey={obj}&key={self.access_key}"
            print("Downloading", filename.name)

            with self.session.get(url, stream=True) as response:
                response.raise_for_status()
                with open(filename, "wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        handle.write(chunk)

    def _is_wanted(self, obj: str, product: str) -> bool:
        if product in obj:
            return True
        # Occurrence rasters need their band-dates.csv companion for the timeline.
        return product.startswith("occurrence") and obj.endswith("band-dates.csv")

    def _release_for(self, species_code: str) -> tuple[int, list[str]]:
        """
        The release to take a species from, and what it publishes for it: the current one when
        it has anything, 2021 otherwise. A species in neither comes back with nothing to fetch.
        """
        if species_code not in self._releases:
            release = (self.version, self._list_objects(species_code, self.version))
            if not release[1] and self.version != FALLBACK_VERSION:
                fallback = (FALLBACK_VERSION, self._list_objects(species_code, FALLBACK_VERSION))
                if fallback[1]:
                    print(f"Nothing for {species_code} in {self.version}, taking it from {FALLBACK_VERSION}")
                    release = fallback
            self._releases[species_code] = release
        return self._releases[species_code]

    def _list_objects(self, species_code: str, version: int) -> list[str]:
        """
        What eBird publishes for exactly this species.
        """
        response = self.session.get(f"{self.BASE}/list-obj/{version}/{species_code}?key={self.access_key}")
        response.raise_for_status()
        objects: list[str] = response.json()
        # Make sure we're not returning species with the same prefix ('redcro' and 'redcro9')
        return [obj for obj in objects if obj.split("/")[1] == species_code]


def species_over(points: list[tuple[float, float]]) -> list[Species]:
    """
    Every species plausible at any of these points that eBird has a code for, sorted.
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
