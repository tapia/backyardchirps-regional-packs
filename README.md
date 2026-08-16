# Backyard Chirps regional packs

The region packs a [Backyard Chirps](https://github.com/tapia/backyardchirps) station downloads,
and the code that builds them.

A pack is a **box, not a country**. It holds the data that only makes sense for one part of the
world: eBird Status & Trends occurrence rasters cropped to the box, and a range map per species
framed on it. Everything else a station needs ships with the station.

A pack carries **no species list**. A list is only right for the point it was made for, and a
box-wide one would make the rare-species rule meaningless, so every station derives its own from
its own coordinates.

## Why this is not in the station repository

Two reasons, and the first is the one that matters:

- **Dependencies.** Drawing a range map needs contextily, geopandas and shapely. A station would
  install that stack on a Raspberry Pi and never open it. Nothing in a pack is built on a Pi.
- **Lifecycles.** The station is tagged in semver. A pack is dated, and gets rebuilt when eBird
  publishes a new data year. Hanging a large pack off a station release makes it look like part
  of the station and forces a station tag whenever a pack changes.

This repository depends on `backyardchirps`, pinned to a release, and imports it. That direction
is deliberate: the call deciding which species are plausible somewhere,
`plausible_species_names_over`, is **shared rather than copied**. A second implementation here
would drift from the stations it serves, and the drift would be silent: one species, on one
station, with no seasonality chart.

The pin is a tag rather than a branch, so a pack is built against a station that exists rather
than against whatever `main` was that morning. Raise it deliberately when this repository needs
something newer.

## Building a pack

```bash
make sync
make models                            # GeoModel, into work/

EBIRD_API_KEY=<your-key> make iberian-peninsula
make publish ID=iberian-peninsula      # then commit index.json
```

Go through the Makefile rather than calling the tool directly. One version string has to reach
three places that agree: the pack file name, the release tag, and the download URL written into
`index.json`. Out of step, the index sends every station to a 404 and the only fix is a new
index. The Makefile derives all three from one value and refuses to publish when they disagree.

`make help` lists the packs already defined. A new one is four lines in the Makefile, on purpose:
a bounding box goes into `pack.json` and `index.json`, where a mistyped one is expensive to take
back. To try a box before committing to it, `make preview ID=... BBOX="W S E N"` skips the maps
and writes no index entry.

Underneath, the builder asks GeoModel which species are plausible over a grid of points covering
the box, downloads the eBird data for each of them, crops every raster to the box, draws a range
map per species, and writes `<id>-<version>.tar.zst`:

```bash
EBIRD_API_KEY=<your-key> uv run build-pack \
    --id iberian-peninsula \
    --name-en "Iberian Peninsula" --name-es "Península ibérica" \
    --bbox -10.8 34.2 5.4 44.9 \
    --output-dir dist
```

Expect the first build to take hours. The download dominates, and the maps are not free either.
A later pack over neighbouring ground re-downloads nothing, because everything lands in `work/`.

| Flag | For |
|---|---|
| `--grid-step` | How far apart, in degrees, GeoModel is asked about the box. Finer costs 48 model runs per extra point and catches a species living in a narrow strip |
| `--skip-download` | Build from what is already downloaded. What to use while working on the crop or the maps |
| `--skip-maps` | Leave `range_maps/` empty. Much faster, and costs a station only the map on a species page |
| `--version` | The pack version, today's date by default, so a later pack sorts above an earlier one |
| `--index`, `--base-url` | Merge this pack's entry into a packs index |

Everything downloaded or generated lands in `work/`, which is git-ignored. That is the station's
own data directory layout, pointed here through `BACKYARDCHIRPS_DATA_DIR`, so a second pack over
neighbouring ground re-fetches nothing. Set the variable yourself to share a directory with a
station checkout that already has the models.

## What a pack contains

```
pack.json                     id, en/es names, bbox, version, species count
ebird_occurrence/<code>/      the cropped raster and its band dates, one directory per species
range_maps/<slug>.webp        the range map, framed on the box
```

Cropping is the whole reason a pack is downloadable. eBird publishes each species as a
whole-world raster at 9km and a station only ever samples the one point it sits on, so the box
takes each file down by two orders of magnitude. Values inside the box are copied untouched,
along with the projection, the pixel grid, the data type and the compression, so a station reads
a cropped raster exactly as it reads a whole one.

## The index

`--index` merges the pack into a JSON file listing every pack with its box, which is what
resolves a station's coordinates to the pack covering them without downloading a candidate to
find out. Each entry repeats `pack.json` and adds the download URL, the size and the sha256.
Rebuilding a pack replaces its entry and leaves the others alone.

## Range maps

Each map is drawn from the eBird `range_smooth_9km` GeoPackage for the species. The seasonal
polygons collapse into four categories (resident, breeding, non-breeding, migration), each one
subtracted from the lower ones so no two ever cover the same ground. Ground that is both breeding
and non-breeding becomes year-round, which is eBird's own convention and what stops a partial
migrant being drawn as a summer bird.

They are laid over label-free CartoDB Positron tiles darkened by an Esri shaded-relief layer, so
mountain ranges show through. The basemap is fetched once per pack, whatever the number of
species.

## Licence

AGPL-3.0-only. The data is not ours: see the station's `NOTICE` for eBird Status & Trends, and
the basemap providers' own terms for the tiles a range map is drawn on.
