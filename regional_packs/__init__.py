"""
Build the region packs a Backyard Chirps station downloads.

Importing anything from this package configures Django first, because everything here reaches
into `backyardchirps` and those modules read settings as they load.

The working directory is the part worth understanding. `backyardchirps` derives its `BASE_DIR`
from where its own settings module sits, so an installed copy resolves it inside `site-packages`.
Left alone, this tool would write its refreshed taxonomy and download the acoustic models in
there, and lose the lot on the next `uv sync`. Setting `BACKYARDCHIRPS_DATA_DIR` moves all of it
into `work/` beside this checkout instead: the models, the eBird rasters, and everything else a
station would keep in its own data directory. Point the variable somewhere else to share a
directory with a station checkout that has already downloaded the models.

There is no database in any of this. Nothing here reads or writes one, and `django.setup()` does
not open a connection.
"""

import os
from pathlib import Path

import django

REPO_ROOT = Path(__file__).resolve().parent.parent

WORK_DIR = Path(os.environ.get("BACKYARDCHIRPS_DATA_DIR") or REPO_ROOT / "work")
WORK_DIR.mkdir(parents=True, exist_ok=True)

os.environ["BACKYARDCHIRPS_DATA_DIR"] = str(WORK_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "backyardchirps.settings")
# The station refuses to start without one. Nothing here signs anything, so any value does.
os.environ.setdefault("SECRET_KEY", "not-used-by-this-tool")

django.setup()
