"""
Reference recordings for the species in a pack, from xeno-canto.

A species page on a station offers a few example recordings, so somebody can compare what the
microphone heard with what the species sounds like. Searching xeno-canto needs an API key, but
playing a recording does not: the files sit at ordinary addresses. So the search happens here,
once, while a pack is built, and the pack carries the addresses. Nobody running a station needs
an account, which is the whole point of doing it here.

The pack gets one <slug>.json per species, holding at most MAX_RECORDINGS entries in the order a
species page shows them. A species with no recordings gets no file at all, which a station reads
as none: an empty file per missing species would be thousands of files saying nothing.
"""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests
from backyardchirps.features.species.entity import Species

API_URL = "https://xeno-canto.org/api/3/recordings"

# What a species page draws. Anything beyond this would be shipped to every station and never
# shown.
MAX_RECORDINGS = 5

# Recordings graded better than this are kept. The grades run A to E and are the recordist's own
# judgement, so this is what keeps a distant, wind-blown recording off a page meant for comparing
# a call against.
MIN_QUALITY = "C"

# Between searches. A pack is hundreds of species, and this is one request each against a free
# service run by volunteers. It costs a few minutes on a build that already takes hours.
PAUSE_SECONDS = 1.0

REQUEST_TIMEOUT_SECONDS = 30

# What a station will not show, so there is no reason to carry it. xeno-canto writes this in the
# fields a recordist was unsure about.
_UNCERTAIN = "uncertain"


def write_reference_calls(
    species: list[Species],
    destination_dir: Path,
    api_key: str,
    report: Callable[[str], None] = lambda message: None,
) -> int:
    """
    Search xeno-canto for every species and write what it finds into destination_dir, returning
    how many species ended up with a file.

    A search that fails costs that one species and nothing else. A pack is worth building without
    one bird's example recordings, and the run behind this is long enough that raising here would
    throw away hours of cropping and drawing.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    nothing_found = []
    failed = []
    for index, one_species in enumerate(species):
        if index:
            time.sleep(PAUSE_SECONDS)
        try:
            recordings = fetch_reference_calls(api_key, one_species.scientific_name)
        except (requests.RequestException, ValueError):
            failed.append(one_species.scientific_name)
            continue

        if not recordings:
            nothing_found.append(one_species.scientific_name)
            continue

        path = destination_dir / f"{one_species.slug}.json"
        path.write_text(json.dumps(recordings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written += 1

    if nothing_found:
        report(f"no recording above {MIN_QUALITY} for {len(nothing_found)} species: {', '.join(nothing_found)}")
    if failed:
        report(f"the search failed for {len(failed)} species: {', '.join(failed)}")
    return written


def fetch_reference_calls(api_key: str, scientific_name: str) -> list[dict[str, Any]]:
    """
    Up to MAX_RECORDINGS recordings of one species, in the shape a station reads.

    Raises on a request that fails or an answer that is not the JSON this expects, so that the
    caller can tell a species with no recordings from a search that never happened.
    """
    response = requests.get(
        API_URL,
        params={"query": f'sp:"{scientific_name}" q:">{MIN_QUALITY}"', "key": api_key},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    found = response.json().get("recordings", [])
    if not isinstance(found, list):
        raise ValueError(f"xeno-canto answered with no list of recordings for {scientific_name}")

    recordings = []
    for entry in found:
        if not isinstance(entry, dict):
            continue
        url = _audio_url(entry.get("file"))
        if url is None:
            continue
        recordings.append(
            {
                "url": url,
                "type": _field(entry.get("type")),
                "sex": _field(entry.get("sex")),
                "stage": _field(entry.get("stage")),
                "length": _field(entry.get("length")),
            }
        )
        if len(recordings) == MAX_RECORDINGS:
            break
    return recordings


def _audio_url(value: object) -> str | None:
    """
    The address a station will play, or None when there is nothing playable.

    xeno-canto gives the file without a scheme, as //xeno-canto.org/..., and a station refuses
    anything that is not https: a page served over https cannot play audio fetched over http, so
    an address that is not upgraded here is one nobody can hear.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    if url.startswith("//"):
        return f"https:{url}"
    if url.startswith("http://"):
        return f"https://{url.removeprefix('http://')}"
    return url if url.startswith("https://") else None


def _field(value: object) -> str | None:
    """
    One of the descriptive fields, or None where a station draws a dash.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == _UNCERTAIN:
        return None
    return text
