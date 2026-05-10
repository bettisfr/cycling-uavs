# Cycling UAVs - Track Exporter

Unified Python tooling for:
- exporting Strava activities to GPX
- exporting FlightAware history pages to CSV
- visualizing all tracks on one map with a time slider

## Setup

```bash
cd /home/fra/Desktop/github/cycling-uavs
/home/fra/pyvenv/bin/pip install -r requirements.txt
```

## Unified CLI

Base command:

```bash
/home/fra/pyvenv/bin/python tracker_export.py <provider> [options]
```

Providers:
- `strava`
- `flightaware`

Default output folders:
- `giro_2026/courses/` for Strava GPX
- `giro_2026/flights/` for FlightAware CSV

Catalog-aware output folders (when `--stage-id` is used):
- `giro_2026/courses/Sxx/` for Strava GPX
- `giro_2026/flights/Sxx/` for FlightAware CSV

## Strava Export (Web Session Only)

```bash
/home/fra/pyvenv/bin/python tracker_export.py strava \
  --local-tz Europe/Rome \
  "https://www.strava.com/activities/17805250943"
```

Notes:
- Strava export uses your web session cookie (`_strava4_session`).
- You can set `STRAVA_SESSION_COOKIE`, or rely on browser cookie extraction.
- `--local-tz` is recommended when `startDateLocal` must be interpreted as local wall time.

Manual cookie example:

```bash
STRAVA_SESSION_COOKIE='...' /home/fra/pyvenv/bin/python tracker_export.py strava "https://www.strava.com/activities/17805250943"
```

Catalog-aware Strava export (auto-updates `giro_2026/stage_links/Sxx.json`):

```bash
STRAVA_SESSION_COOKIE='...' /home/fra/pyvenv/bin/python tracker_export.py strava \
  --stage-id S01 \
  --rider-id B001 \
  --local-tz Europe/Rome \
  "https://www.strava.com/activities/17805250943"
```

## FlightAware Export

```bash
/home/fra/pyvenv/bin/python tracker_export.py flightaware \
  "https://it.flightaware.com/live/flight/MSA94S/history/20260323/2110Z/LICA/LIPO"
```

Catalog-aware FlightAware export (auto-updates `giro_2026/stages.json` flight fields):

```bash
/home/fra/pyvenv/bin/python tracker_export.py flightaware \
  --stage-id S01 \
  "https://it.flightaware.com/live/flight/MSA94S/history/20260323/2110Z/LICA/LIPO"
```

## Map Visualization

Generate interactive map:

```bash
/home/fra/pyvenv/bin/python visualize_tracks.py
```

Output:
- `giro_2026/html/maps/map_tracks.html`

## Stage Workflow (4 Commands)

Example for `S03`:

```bash
# 1) Import raw stage HTML -> updates S03.json + S03.html + index.html
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/import_stage_raw.py --stage-id S03
```

```bash
# 2) Import flight track -> saves CSV + updates stages.json flight section
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/tracker_export.py flightaware \
  --stage-id S03 \
  "https://it.flightaware.com/live/flight/ASR251/history/20260510/0000Z/AAAA/BBBB"
```

```bash
# 3) Download missing rider GPX for stage
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/download_stage_gpx.py --stage-id S03
```

```bash
# 4) Build stage map (air + selected riders) and auto-refresh stage index map links
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/visualize_tracks.py \
  --stage-id S03 \
  --bibs 6 131 192 187 176 \
  --flight-offset-min 60 \
  -o /home/fra/Desktop/github/cycling-uavs/giro_2026/html/maps/S03.html
```

## PCS Startlist -> Strava Profiles

If you saved a ProCyclingStats startlist HTML locally, you can download all rider pages and extract Strava athlete URLs:

```bash
/home/fra/pyvenv/bin/python pcs_strava_from_startlist.py \
  --startlist-html "/home/fra/Downloads/Startlist for Giro d'Italia 2026.html" \
  --output-json giro_2026/pcs_strava_links.json \
  --update-riders-json giro_2026/riders.json
```

This script also caches rider pages in `/tmp/cycling-uavs-riders/pages`.


## Giro 2026 Output Paths

When using stage-aware exports, files are organized by stage:

- Rider GPX: `giro_2026/courses/Sxx/`
  - example: `giro_2026/courses/S01/B001__activity_123456789.gpx`
- Flight CSV: `giro_2026/flights/Sxx/`
  - example: `giro_2026/flights/S01/ASR132_track.csv`

Catalog files updated in parallel:

- Rider-stage links: `giro_2026/stage_links/Sxx.json`
- Flight metadata: `giro_2026/stages.json` (`stages[].flight`)

## Raw HTML Debug Workflow

If needed for debugging/reproducibility, save raw activity pages in:
- `giro_2026/raw/`

This lets you inspect and parse local copies without new network requests.
