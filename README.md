# Cycling UAVs - Track Exporter

Unified Python tooling for:
- exporting Strava activities to GPX
- exporting FlightAware history pages to CSV
- visualizing all tracks on one map with a time slider

## Setup

```bash
cd /home/fra/Desktop/github/cycling-uavs
/home/fra/uavenv/bin/pip install -r requirements.txt
```

## Unified CLI

Base command:

```bash
/home/fra/uavenv/bin/python tracker_export.py <provider> [options]
```

Providers:
- `strava`
- `flightaware`

Default output folders:
- `output/courses/` for Strava GPX
- `output/flights/` for FlightAware CSV

## Strava Export (Web Session Only)

```bash
/home/fra/uavenv/bin/python tracker_export.py strava \
  --local-tz Europe/Rome \
  "https://www.strava.com/activities/17805250943"
```

Notes:
- Strava export uses your web session cookie (`_strava4_session`).
- You can set `STRAVA_SESSION_COOKIE`, or rely on browser cookie extraction.
- `--local-tz` is recommended when `startDateLocal` must be interpreted as local wall time.

Manual cookie example:

```bash
STRAVA_SESSION_COOKIE='...' /home/fra/uavenv/bin/python tracker_export.py strava "https://www.strava.com/activities/17805250943"
```

## FlightAware Export

```bash
/home/fra/uavenv/bin/python tracker_export.py flightaware \
  "https://it.flightaware.com/live/flight/MSA94S/history/20260323/2110Z/LICA/LIPO"
```

## Map Visualization

Generate interactive map:

```bash
/home/fra/uavenv/bin/python visualize_tracks.py
```

Output:
- `output/map_tracks.html`

## Raw HTML Debug Workflow

If needed for debugging/reproducibility, save raw activity pages in:
- `output/raw/strava/`

This lets you inspect and parse local copies without new network requests.
