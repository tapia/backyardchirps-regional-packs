"""
Turning a xeno-canto answer into the files a pack carries.

Nothing here goes near the network. What is under test is the shape a station will read: which
recordings survive, what an unplayable address costs, and that a species with nothing found gets
no file rather than an empty one.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import requests
from backyardchirps.features.species.entity import Species

from regional_packs import xeno_canto
from regional_packs.xeno_canto import fetch_reference_calls
from regional_packs.xeno_canto import write_reference_calls

SWALLOW = Species("Hirundo rustica")
BLACKBIRD = Species("Turdus merula")


class _Answer:
    """
    Stands in for a requests response, with only what the module reads from one.
    """

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._payload


@pytest.fixture(autouse=True)
def no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The politeness pause between searches is real time, and the tests do not need it.
    """
    monkeypatch.setattr(xeno_canto.time, "sleep", lambda seconds: None)


def _answering(monkeypatch: pytest.MonkeyPatch, by_species: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Answer each search from by_species, keyed by scientific name, and record what was asked.
    """
    asked: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Answer:
        asked.append(params)
        for scientific_name, payload in by_species.items():
            if scientific_name in params["query"]:
                return _Answer(payload)
        return _Answer({"recordings": []})

    monkeypatch.setattr(xeno_canto.requests, "get", fake_get)
    return asked


def test_a_recording_becomes_what_a_station_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(
        monkeypatch,
        {
            "Hirundo rustica": {
                "recordings": [
                    {
                        "file": "//xeno-canto.org/1/download",
                        "type": "song",
                        "sex": "male",
                        "stage": "adult",
                        "length": "0:32",
                    }
                ]
            }
        },
    )

    assert fetch_reference_calls("a-key", "Hirundo rustica") == [
        {
            "url": "https://xeno-canto.org/1/download",
            "type": "song",
            "sex": "male",
            "stage": "adult",
            "length": "0:32",
        }
    ]


def test_the_search_asks_for_the_species_and_a_quality_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _answering(monkeypatch, {})

    fetch_reference_calls("a-key", "Hirundo rustica")

    assert asked[0]["key"] == "a-key"
    assert 'sp:"Hirundo rustica"' in asked[0]["query"]
    assert f'q:">{xeno_canto.MIN_QUALITY}"' in asked[0]["query"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("uncertain", None),
        ("UNCERTAIN", None),
        ("  ", None),
        (None, None),
        ("  song  ", "song"),
    ],
)
def test_fields_a_recordist_left_open_become_null(
    monkeypatch: pytest.MonkeyPatch, value: object, expected: str | None
) -> None:
    _answering(monkeypatch, {"Hirundo rustica": {"recordings": [{"file": "//x.org/1", "type": value}]}})

    assert fetch_reference_calls("a-key", "Hirundo rustica")[0]["type"] == expected


@pytest.mark.parametrize(
    ("file_value", "expected"),
    [
        ("//xeno-canto.org/1", "https://xeno-canto.org/1"),
        ("http://xeno-canto.org/1", "https://xeno-canto.org/1"),
        ("https://xeno-canto.org/1", "https://xeno-canto.org/1"),
    ],
)
def test_every_address_is_written_as_https(monkeypatch: pytest.MonkeyPatch, file_value: str, expected: str) -> None:
    """
    A station refuses anything else, since a page served over https cannot play http audio.
    """
    _answering(monkeypatch, {"Hirundo rustica": {"recordings": [{"file": file_value}]}})

    assert fetch_reference_calls("a-key", "Hirundo rustica")[0]["url"] == expected


def test_recordings_with_no_file_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(
        monkeypatch,
        {"Hirundo rustica": {"recordings": [{"file": ""}, {"type": "song"}, {"file": "//x.org/good"}]}},
    )

    assert [call["url"] for call in fetch_reference_calls("a-key", "Hirundo rustica")] == ["https://x.org/good"]


def test_no_more_than_a_species_page_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(
        monkeypatch,
        {"Hirundo rustica": {"recordings": [{"file": f"//x.org/{number}"} for number in range(30)]}},
    )

    assert len(fetch_reference_calls("a-key", "Hirundo rustica")) == xeno_canto.MAX_RECORDINGS


def test_an_answer_that_is_not_a_list_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _answering(monkeypatch, {"Hirundo rustica": {"recordings": "no recordings for you"}})

    with pytest.raises(ValueError, match="Hirundo rustica"):
        fetch_reference_calls("a-key", "Hirundo rustica")


def test_a_file_per_species_named_by_slug(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _answering(
        monkeypatch,
        {
            "Hirundo rustica": {"recordings": [{"file": "//x.org/swallow", "type": "song"}]},
            "Turdus merula": {"recordings": [{"file": "//x.org/blackbird"}]},
        },
    )

    written = write_reference_calls([SWALLOW, BLACKBIRD], tmp_path, api_key="a-key")

    assert written == 2
    assert json.loads((tmp_path / "hirundo-rustica.json").read_text())[0]["url"] == "https://x.org/swallow"
    assert json.loads((tmp_path / "turdus-merula.json").read_text())[0]["url"] == "https://x.org/blackbird"


def test_a_species_with_nothing_found_gets_no_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    A station reads a missing file as no recordings, so an empty one would be thousands of files
    in a pack saying nothing.
    """
    _answering(monkeypatch, {"Hirundo rustica": {"recordings": [{"file": "//x.org/swallow"}]}})

    written = write_reference_calls([SWALLOW, BLACKBIRD], tmp_path, api_key="a-key")

    assert written == 1
    assert not (tmp_path / "turdus-merula.json").exists()


def test_a_failed_search_costs_only_that_species(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """
    Reference calls are the last thing a pack gets, after hours of downloading and drawing. One
    species that xeno-canto will not answer for must not throw that away.
    """
    reported: list[str] = []

    def fake_get(url: str, params: dict[str, Any], timeout: float) -> _Answer:
        if "Turdus merula" in params["query"]:
            raise requests.ConnectionError("xeno-canto is down")
        return _Answer({"recordings": [{"file": "//x.org/swallow"}]})

    monkeypatch.setattr(xeno_canto.requests, "get", fake_get)

    written = write_reference_calls([SWALLOW, BLACKBIRD], tmp_path, api_key="a-key", report=reported.append)

    assert written == 1
    assert (tmp_path / "hirundo-rustica.json").is_file()
    assert any("Turdus merula" in line for line in reported)
