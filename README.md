# mapillary-journal
Simple toolkit for Mapillary contributors

Python 3.9+ tools that save **metadata only** from the
[Mapillary Graph API](https://www.mapillary.com/developer/api-documentation).
No image files are requested or downloaded. No Python packages are required.

## Quick start

Create an access token in the Mapillary developer dashboard. Supply it through
the environment, not source files or command-line arguments:

```sh
read -r -s -p "Mapillary access token: " MAPILLARY_ACCESS_TOKEN
export MAPILLARY_ACCESS_TOKEN
python manager.py update --username YOUR_MAPILLARY_USERNAME
```

Open `data/index.html` in your browser. It embeds your saved metadata and works
without a local server, but requires internet access for Leaflet (pinned to 1.9.4
on unpkg), OpenStreetMap tiles, and outbound Mapillary links. No API token is
included in the HTML. Treat generated files as location-history data: keep them
private unless you intend to publish them.

Run `python manager.py update` again to sync and regenerate the map and device
catalog. Setup reuses `config.json` without another user lookup. With a user
access token you can omit the username on first setup to use `/me`. For an account
without public images, use `--user-id ID`; username-only setup resolves the user
ID from the creator of a public image. To change accounts, use a separate data
directory rather than mixing users' histories.

## Separate tools

```sh
python setup_user.py --username YOUR_MAPILLARY_USERNAME
python sync_sequences.py
python capture_devices.py
python generate_map.py
```

The manager provides the same operations as `setup`, `sync`, `devices`, and `map`.
`update` runs all four in order. Every tool supports `--data-dir PATH` (default:
`data`). Map generation also supports `--output PATH`. Device and map generation
work offline from saved data; setup and sync need a token.

## Stored data and incremental sync

- `config.json`: username and user ID, never a token.
- `sequences/SEQUENCE_ID.geojson`: one FeatureCollection per sequence, with a
  LineString (or Point for a single image), capture-time bounds, devices, image
  types, a Mapillary link, and individual image IDs, coordinates and timestamps.
- `devices.json`: unique manufacturer/model/type combinations, classified as
  `360` or `normal`, with image and sequence counts. Missing device names are
  shown as `Unknown`; the same device can appear in both classifications.
- `sync.json`: last successful sync time and totals.
- `index.html`: full-screen static desktop map.

The first sync follows every `/images?creator_username=...` pagination page and
groups image metadata by sequence. Later syncs still scan **IDs and sequence IDs**
to discover additions, but request detailed metadata only for unseen images.
Existing sequence files are untouched unless new images need to be appended.
This avoids a capture-date watermark, which would miss newly uploaded sequences
captured in the past. Image IDs are deduplicated and ordered by capture time.
Completed discovery is required before writing updates; interrupted runs can be
retried. This is an append-only journal: API deletions and edits to already saved
images are not mirrored. A fresh data directory performs a full rebuild.

Coverage is limited to images the API exposes for the configured user. Images
still processing or lacking required geometry/time/type metadata cause sync to
stop without marking them as downloaded; retry once processing finishes.
Rate-limit and transient server errors receive bounded retries.

## Map controls

Use the two bottom timeline sliders or UTC date inputs to crop the capture-date
range (both selected dates are inclusive). Filter by normal/360 imagery and
capture device. Cropping acts on individual image locations, not just sequence
start dates. Excluded points split lines so unrelated segments are not connected.
Click a sequence to open its selected images in Mapillary. **Fit visible** zooms
to matching data; **Reset filters** restores the full journal. Green denotes 360
imagery, blue denotes normal imagery. Empty journals and unavailable basemaps
display explanatory messages.

## Tests

Run the offline regression tests with `python -m unittest -v test_journal`.
They use only the standard library and fake API responses; they do not need an
access token or download images.
