# Building and publishing region packs.
#
# The point of this file is that one version string reaches three places that have to agree: the
# pack file name, the release tag, and the download URL baked into index.json. Getting those out
# of step publishes an index that sends every station to a 404, and the only fix is a new index.
# So VERSION is computed once here and everything else is derived from it.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

REPO := tapia/backyardchirps-regional-packs
INDEX := index.json
DIST := dist

# Today, UTC, which is what build-pack would have chosen on its own. Override to rebuild an
# existing pack under its own version: make iberian-peninsula VERSION=2026-08-16
VERSION ?= $(shell date -u +%F)

TAG = $(ID)-$(VERSION)
TARBALL = $(DIST)/$(ID)-$(VERSION).tar.zst
BASE_URL = https://github.com/$(REPO)/releases/download/$(TAG)

# Set to anything to build from the rasters already in work/, without asking eBird.
SKIP_DOWNLOAD ?=
DOWNLOAD_FLAG = $(if $(SKIP_DOWNLOAD),--skip-download,)

# Every known pack is a target below. Adding one is four lines here rather than a command
# somebody types, because a bounding box goes into pack.json and into index.json, where a
# mistyped one is expensive to take back.

iberian-peninsula: ID := iberian-peninsula
iberian-peninsula: NAME_EN := Iberian Peninsula
iberian-peninsula: NAME_ES := Península ibérica
iberian-peninsula: BBOX := -10.8 34.2 5.4 44.9
iberian-peninsula: pack

canary-islands: ID := canary-islands
canary-islands: NAME_EN := Canary Islands
canary-islands: NAME_ES := Islas Canarias
canary-islands: BBOX := -18.6 27.4 -13.1 29.8
canary-islands: pack

.PHONY: help
help:
	@echo "Setup"
	@echo "  make sync                    install dependencies"
	@echo "  make models                  download GeoModel into work/"
	@echo "  make check                   lint, type-check and test"
	@echo ""
	@echo "Building a pack (needs EBIRD_API_KEY and XENO_CANTO_API_KEY)"
	@echo "  make iberian-peninsula       build it, maps and all, and update $(INDEX)"
	@echo "  make canary-islands"
	@echo "  make box-image ID=... BBOX=... draw the box on a map, to check it covers what you meant"
	@echo "  make preview ID=... BBOX=...  a quick look at a new box: no maps, no index entry"
	@echo ""
	@echo "Publishing"
	@echo "  make publish ID=...          create the release and upload the pack"
	@echo ""
	@echo "Flags"
	@echo "  VERSION=2026-08-16           rebuild under an existing version (default: today, UTC)"
	@echo "  SKIP_DOWNLOAD=1              build from what is already in work/"

.PHONY: sync
sync:
	uv sync

.PHONY: models
models:
	uv run download-models

.PHONY: check
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	uv run pytest -q

.PHONY: pack
pack: require-pack-arguments
	@test -n "$${EBIRD_API_KEY:-}" || test -n "$(SKIP_DOWNLOAD)" || \
		{ echo "Set EBIRD_API_KEY, or pass SKIP_DOWNLOAD=1 to build from work/."; exit 1; }
	@test -n "$${XENO_CANTO_API_KEY:-}" || \
		{ echo "Set XENO_CANTO_API_KEY. It is at https://xeno-canto.org/account."; exit 1; }
	uv run build-pack \
		--id "$(ID)" \
		--name-en "$(NAME_EN)" --name-es "$(NAME_ES)" \
		--bbox $(BBOX) \
		--version "$(VERSION)" \
		--output-dir "$(DIST)" \
		--index "$(INDEX)" \
		--base-url "$(BASE_URL)" \
		$(DOWNLOAD_FLAG)
	@echo
	@echo "Built $(TARBALL) and updated $(INDEX)."
	@echo "Next: make publish ID=$(ID) VERSION=$(VERSION)"

# A box you are still deciding on. No maps and no reference calls, so it finishes in minutes
# rather than hours and needs no xeno-canto key, and no --index, so nothing a station reads is
# touched by a pack you are not going to publish.
.PHONY: preview
preview: require-pack-arguments
	uv run build-pack \
		--id "$(ID)" \
		--name-en "$(or $(NAME_EN),Preview)" --name-es "$(or $(NAME_ES),Preview)" \
		--bbox $(BBOX) \
		--version "$(VERSION)" \
		--output-dir "$(DIST)/preview" \
		--skip-maps \
		--skip-reference-calls \
		$(DOWNLOAD_FLAG)

# Look at a box before anything is built from it. Seconds, no eBird key, no species. The only
# check there is that a box covers the ground its name claims: nothing arithmetic can tell you
# that an island is missing.
.PHONY: box-image
box-image: require-pack-arguments
	uv run box-image --bbox $(BBOX) --output "$(DIST)/boxes/$(ID).png"

.PHONY: publish
publish:
	@test -n "$(ID)" || { echo "publish needs ID, for example: make publish ID=iberian-peninsula"; exit 1; }
	@test -f "$(TARBALL)" || { echo "No $(TARBALL). Build it first, or pass the VERSION you built."; exit 1; }
	@# The check that matters. index.json holds the URL stations will fetch, and that URL contains
	@# the tag. If the two disagree the release goes up and every station gets a 404.
	@grep -q "$(BASE_URL)/$(notdir $(TARBALL))" "$(INDEX)" || \
		{ echo "$(INDEX) does not point at $(TAG). Rebuild the pack so the two agree."; exit 1; }
	gh release create "$(TAG)" "$(TARBALL)" \
		--repo "$(REPO)" \
		--title "$(ID) $(VERSION)" \
		--notes "Region pack for $(ID), built $(VERSION)."
	@echo
	@echo "Published. Now commit $(INDEX): it is what stations read to find this pack."

.PHONY: require-pack-arguments
require-pack-arguments:
	@test -n "$(ID)" || { echo "Needs ID. Try 'make help' for the packs already defined."; exit 1; }
	@test -n "$(BBOX)" || { echo "Needs BBOX, as: west south east north"; exit 1; }

.PHONY: clean
clean:
	rm -rf "$(DIST)"
