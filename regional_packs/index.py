"""
The index a station resolves its coordinates against.
"""

import json
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any


def update_index(
    index_path: Path,
    base_url: str,
    pack: dict[str, Any],
    filename: str,
    checksum: str,
    size_bytes: int,
) -> None:
    """
    Merge this pack into the index. The entry repeats pack.json so that resolving a box needs
    one file rather than one download per candidate, and adds what a download needs: where the
    file is, how big it is, and what it should hash to.

    Rebuilding a pack replaces its entry and leaves every other alone, so an index is built up
    one pack at a time rather than in a single run over all of them.
    """
    index: dict[str, Any] = {"packs": []}
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))

    entry = {
        **pack,
        "url": f"{base_url.rstrip('/')}/{filename}",
        "sha256": checksum,
        "size_bytes": size_bytes,
    }
    others = [other for other in index.get("packs", []) if other.get("id") != entry["id"]]
    index["packs"] = sorted([*others, entry], key=lambda one_pack: str(one_pack["id"]))
    index["updated"] = datetime.now(timezone.utc).date().isoformat()

    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
