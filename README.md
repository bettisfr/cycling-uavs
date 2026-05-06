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
- `output/courses/` for Strava GPX
- `output/flights/` for FlightAware CSV

Catalog-aware output folders (when `--stage-id` is used):
- `output/giro_2026/courses/Sxx/` for Strava GPX
- `output/giro_2026/flights/Sxx/` for FlightAware CSV

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
- `output/map_tracks.html`

## PCS Startlist -> Strava Profiles

If you saved a ProCyclingStats startlist HTML locally, you can download all rider pages and extract Strava athlete URLs:

```bash
/home/fra/pyvenv/bin/python pcs_strava_from_startlist.py \
  --startlist-html "/home/fra/Downloads/Startlist for Giro d'Italia 2026.html" \
  --output-json output/giro_2026/pcs_strava_links.json \
  --update-riders-json giro_2026/riders.json
```

This script also caches rider pages in `/tmp/cycling-uavs-riders/pages`.

## Raw HTML Debug Workflow

If needed for debugging/reproducibility, save raw activity pages in:
- `output/raw/strava/`

This lets you inspect and parse local copies without new network requests.
