"""
Which eBird release a species is taken from, and what its files are called once downloaded.

Nothing here goes near the network. eBird is a fake that publishes whatever each test says, in
the layout the real listing has, and records what it was asked for.
"""

from pathlib import Path
from typing import Any

from regional_packs.ebird import OCCURRENCE_PRODUCT
from regional_packs.ebird import RANGE_PRODUCT
from regional_packs.ebird import EbirdDownloader

# What the real listings hold for one species in each release, trimmed to the files that decide
# anything: the ones a pack wants and the neighbours it must not take by mistake.
CURRENT = [
    "2023/eurrob1/config.json",
    "2023/eurrob1/weekly/eurrob1_occurrence_median_3km_2023.tif",
    "2023/eurrob1/weekly/eurrob1_occurrence_median_9km_2023.tif",
    "2023/eurrob1/weekly/band-dates.csv",
    "2023/eurrob1/ranges/eurrob1_range_smooth_9km_2023.gpkg",
    "2023/eurrob1/ranges/eurrob1_range_smooth_27km_2023.gpkg",
]
FALLBACK = [
    "2021/comshe/config.json",
    "2021/comshe/weekly/comshe_occurrence_median_hr_2021.tif",
    "2021/comshe/weekly/comshe_occurrence_median_lr_2021.tif",
    "2021/comshe/weekly/comshe_occurrence_median_mr_2021.tif",
    "2021/comshe/weekly/band-dates.csv",
    "2021/comshe/seasonal/comshe_occurrence_seasonal_mean_mr_2021.tif",
    "2021/comshe/ranges/comshe_range_raw_mr_2021.gpkg",
    "2021/comshe/ranges/comshe_range_smooth_lr_2021.gpkg",
    "2021/comshe/ranges/comshe_range_smooth_mr_2021.gpkg",
]


class _Answer:
    """
    Stands in for a requests response, with only what the downloader reads from one.
    """

    def __init__(self, payload: Any = None, content: bytes = b"") -> None:
        self._payload = payload
        self._content = content

    def __enter__(self) -> "_Answer":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return [self._content]


class _Ebird:
    """
    Publishes the given objects under list-obj/<version>/<code>, and nothing anywhere else.

    Listing matches codes by prefix, the way the real endpoint does: asking for redcro answers
    with redcro9's files as well.
    """

    def __init__(self, published: dict[tuple[int, str], list[str]]) -> None:
        self.published = published
        self.listed: list[tuple[int, str]] = []
        self.fetched: list[str] = []

    def get(self, url: str, stream: bool = False) -> _Answer:
        if "/list-obj/" in url:
            version, code = url.split("/list-obj/")[1].split("?")[0].split("/")
            self.listed.append((int(version), code))
            return _Answer(
                [
                    obj
                    for (published_version, published_code), objects in self.published.items()
                    if published_version == int(version) and published_code.startswith(code)
                    for obj in objects
                ]
            )
        obj = url.split("objKey=")[1].split("&")[0]
        self.fetched.append(obj)
        return _Answer(content=obj.encode())


def _downloader(ebird: _Ebird) -> EbirdDownloader:
    downloader = EbirdDownloader("a-key")
    downloader.session = ebird  # type: ignore[assignment]
    return downloader


def _download(downloader: EbirdDownloader, code: str, output_dir: Path) -> list[str]:
    for product in (OCCURRENCE_PRODUCT, RANGE_PRODUCT):
        downloader.download_species(code, output_dir, product)
    return sorted(path.name for path in (output_dir / code).iterdir())


def test_takes_a_species_from_the_current_release_when_it_is_there(tmp_path: Path) -> None:
    ebird = _Ebird({(2023, "eurrob1"): CURRENT, (2021, "eurrob1"): FALLBACK})

    files = _download(_downloader(ebird), "eurrob1", tmp_path)

    assert files == [
        "band-dates.csv",
        "eurrob1_occurrence_median_9km_2023.tif",
        "eurrob1_range_smooth_9km_2023.gpkg",
    ]
    # 2021 is never asked about a species the current release has, even though it has it too.
    assert all(version == 2023 for version, _ in ebird.listed)


def test_falls_back_to_2021_for_a_species_the_current_release_left_out(tmp_path: Path) -> None:
    ebird = _Ebird({(2021, "comshe"): FALLBACK})

    files = _download(_downloader(ebird), "comshe", tmp_path)

    # Saved under the name the builder, the range maps and the station glob for, with the
    # year left as it was so a directory says which release it came from.
    assert files == [
        "band-dates.csv",
        "comshe_occurrence_median_9km_2021.tif",
        "comshe_range_smooth_9km_2021.gpkg",
    ]
    assert sorted(ebird.fetched) == [
        "2021/comshe/ranges/comshe_range_smooth_mr_2021.gpkg",
        "2021/comshe/weekly/band-dates.csv",
        "2021/comshe/weekly/comshe_occurrence_median_mr_2021.tif",
    ]


def test_asks_each_release_about_a_species_once(tmp_path: Path) -> None:
    # Two products per species, so without keeping the listing a fallback species would cost
    # four requests before a single file arrived.
    ebird = _Ebird({(2021, "comshe"): FALLBACK})

    _download(_downloader(ebird), "comshe", tmp_path)

    assert ebird.listed == [(2023, "comshe"), (2021, "comshe")]


def test_a_species_in_neither_release_gets_nothing(tmp_path: Path) -> None:
    ebird = _Ebird({})

    files = _download(_downloader(ebird), "lottit1", tmp_path)

    assert files == []
    assert ebird.fetched == []


def test_skips_a_fallback_file_already_on_disk(tmp_path: Path) -> None:
    # What is on disk is checked by the name it was saved under, not the name eBird lists, or
    # every rebuild would fetch every fallback species again.
    ebird = _Ebird({(2021, "comshe"): FALLBACK})
    _download(_downloader(ebird), "comshe", tmp_path)
    ebird.fetched.clear()

    _download(_downloader(ebird), "comshe", tmp_path)

    assert ebird.fetched == []


def test_leaves_out_a_species_whose_code_starts_with_this_ones(tmp_path: Path) -> None:
    # redcro9 is Cassia Crossbill. Its files sort ahead of redcro's, so taking them would ship an
    # Idaho endemic's data as Red Crossbill's.
    ebird = _Ebird({(2023, "redcro"): _products(2023, "redcro"), (2023, "redcro9"): _products(2023, "redcro9")})

    files = _download(_downloader(ebird), "redcro", tmp_path)

    assert files == [
        "band-dates.csv",
        "redcro_occurrence_median_9km_2023.tif",
        "redcro_range_smooth_9km_2023.gpkg",
    ]
    assert all(obj.startswith("2023/redcro/") for obj in ebird.fetched)


def test_another_species_files_do_not_stand_in_for_the_fallback(tmp_path: Path) -> None:
    # The current release has nothing for purswa itself, only for purswa3. That is nothing, so
    # 2021 is asked, rather than the listing counting as an answer.
    ebird = _Ebird({(2023, "purswa3"): _products(2023, "purswa3"), (2021, "purswa"): _products(2021, "purswa")})

    files = _download(_downloader(ebird), "purswa", tmp_path)

    assert files == [
        "band-dates.csv",
        "purswa_occurrence_median_9km_2021.tif",
        "purswa_range_smooth_9km_2021.gpkg",
    ]


def _products(version: int, code: str) -> list[str]:
    """
    The files a pack takes from one species' listing, in either release's naming.
    """
    resolution = "9km" if version == 2023 else "mr"
    return [
        f"{version}/{code}/weekly/{code}_occurrence_median_{resolution}_{version}.tif",
        f"{version}/{code}/weekly/band-dates.csv",
        f"{version}/{code}/ranges/{code}_range_smooth_{resolution}_{version}.gpkg",
    ]
