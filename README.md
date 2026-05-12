# Cycling UAVs

Stage-based tooling for Giro datasets:
- import stage raw HTML and update stage catalogs
- export tracks from Strava/FlightAware
- build lightweight stage maps

## Setup

```bash
cd /home/fra/Desktop/github/cycling-uavs
/home/fra/pyvenv/bin/pip install -r requirements.txt
```

## User Entry Points (3 Scripts)

### 1) Import stage raw (`import_stage_raw.py`)

```bash
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/import_stage_raw.py --stage-id S03
```

What it updates:
- `giro_2026/stage_links/Sxx.json`
- `giro_2026/html/stages/Sxx.html`
- `giro_2026/html/stages/index.html`

Raw file resolution:
- first: `giro_2026/raw/S03.txt`
- fallback: `giro_2026/raw/s03.txt`

### 2) Export tracks (`tracker_export.py`)

FlightAware (provider autodetected from URL):

```bash
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/tracker_export.py \
  --stage-id S03 \
  "https://it.flightaware.com/live/flight/ASR251/history/20260510/0000Z/AAAA/BBBB"
```

Strava (provider autodetected from activity URL):

```bash
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/tracker_export.py \
  --stage-id S03 \
  --rider-id B006 \
  --local-tz Europe/Rome \
  "https://www.strava.com/activities/17805250943"
```

Notes:
- Strava export uses `_strava4_session` from `STRAVA_SESSION_COOKIE` or browser cookie extraction.
- with `--stage-id Sxx`, outputs are stage-scoped and catalogs are updated.
- provider is autodetected from input URL (`strava.com/activities/...` or `flightaware.com/...`).

### 3) Build stage map (`visualize_tracks.py`)

```bash
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/visualize_tracks.py \
  --stage-id S03 \
  --bibs 6 131 192 187 176 \
  --flight-offset-min 60
```

Output (default with `--stage-id`):
- `giro_2026/html/maps/S03.html`

Also auto-updates:
- `giro_2026/html/stages/index.html` (Map links)

### Optional: Download all missing rider GPX

```bash
/home/fra/pyvenv/bin/python /home/fra/Desktop/github/cycling-uavs/download_stage_gpx.py --stage-id S03
```

## Practical Notes

- Single data root: everything is under `giro_2026/`.
- Generated heavy artifacts are local-only (ignored by git):
  - `giro_2026/courses/`
  - `giro_2026/flights/`
  - `giro_2026/raw/`
  - `giro_2026/html/`
- Stage pages include:
  - flight status/source
  - GPX presence
  - start time (`HH:mm`, midnight entries highlighted)
  - previous/next stage navigation

## Giro 2026 Output Paths

- Rider GPX: `giro_2026/courses/Sxx/`
  - example: `giro_2026/courses/S01/B001__activity_123456789.gpx`
- Flight CSV: `giro_2026/flights/Sxx/`
  - example: `giro_2026/flights/S01/ASR132_track.csv`

Catalog files updated in parallel:

- Rider-stage links: `giro_2026/stage_links/Sxx.json`
- Flight metadata: `giro_2026/stages.json` (`stages[].flight`)

## Raw HTML Debug Workflow

Save raw stage pages in:
- `giro_2026/raw/`
