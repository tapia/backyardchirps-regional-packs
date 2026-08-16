"""
Fetch the GeoModel this tool needs, into the working directory.

A station gets it through `manage.py download_birdnet3_model`, which this repository does not
have. Both go through the same `refresh_geomodel`, so there is no second copy of the download.

Only the location model is fetched. The acoustic model is what listens to a microphone, and
nothing here does.
"""

import sys
from pathlib import Path

from backyardchirps.features.recording import maintenance
from django.conf import settings

from regional_packs import WORK_DIR


def main() -> None:
    written = maintenance.refresh_geomodel(
        model_destination=Path(settings.GEOMODEL_MODEL_FILE),
        labels_destination=Path(settings.GEOMODEL_LABELS_FILE),
    )
    if written:
        print(f"Downloaded into {WORK_DIR}: {', '.join(written)}", file=sys.stderr)
    else:
        print(f"GeoModel is already current in {WORK_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
