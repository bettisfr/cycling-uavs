# Cycling UAVs

Daily workflow for Giro stage data (Strava riders + FlightAware aircraft), without Strava API.

## Setup

```bash
cd /home/fra/Desktop/github/cycling-uavs
/home/fra/pyvenv/bin/pip install -r requirements.txt
```

## Daily Pipeline

Use `SXX` as stage id (example: `S07`).

### 1) Fetch rider raw pages (Brave + session cookie)

```bash
/home/fra/pyvenv/bin/python fetch_all_rider_raws.py --timeout-sec 20 --headless
```

Outputs:
- `giro_2026/raw/riders/Bxxx.txt`

### 2) Parse rider raws and update stage links

```bash
/home/fra/pyvenv/bin/python import_rider_raw.py --all
```

Outputs/updates:
- `giro_2026/stage_links/SXX.json`

### 3) (Optional) Import stage raw page for one stage

If you also have a stage raw file (`giro_2026/raw/stages/SXX.txt`):

```bash
/home/fra/pyvenv/bin/python import_stage_raw.py --stage-id S07
```

Updates:
- `giro_2026/stage_links/S07.json`

### 4) Download missing rider GPX for one stage

```bash
# all stages
/home/fra/pyvenv/bin/python download_stage_gpx.py --all

# single
/home/fra/pyvenv/bin/python download_stage_gpx.py --stage-id S07
```

Outputs:
- `giro_2026/courses/S07/*.gpx`

### 5) Download aircraft tracks

Single stage:

```bash
/home/fra/pyvenv/bin/python tracker_export.py   --stage-id S07   "https://it.flightaware.com/live/flight/ASR251/history/..."
```

Outputs:
- `giro_2026/flights/SXX/*.csv`
- `giro_2026/stages.json` flight metadata update (`source_urls`, `track_csv_path`, `track_status`)

### 6) Generate stage map

For `S01-S03` use `--flight-offset-min 60`.
For `S04+` use `--flight-offset-min 0`.

```bash
# all stages (auto offset: S01-S03 => 60, S04+ => 0, and all GPX per stage)
/home/fra/pyvenv/bin/python visualize_tracks.py --all

# Example S02 (offset 60)
/home/fra/pyvenv/bin/python visualize_tracks.py --stage-id S02 --course-tracks 9999 --flight-offset-min 60

# Example S07 (offset 0)
/home/fra/pyvenv/bin/python visualize_tracks.py --stage-id S07 --course-tracks 9999 --flight-offset-min 0
```

Output:
- `giro_2026/html/maps/SXX.html`

Note:
- `--course-tracks` should be >= number of GPX you want to include.

### 7) Generate stage planimetry/elevation images

```bash
# all stages
/home/fra/pyvenv/bin/python generate_stage_images.py

# single stage
/home/fra/pyvenv/bin/python generate_stage_images.py --stage-id S07
```

Outputs:
- `giro_2026/html/images/SXX_planimetry.png`
- `giro_2026/html/images/SXX_elevation.png`

### 8) Refresh all HTML pages

```bash
/home/fra/pyvenv/bin/python refresh_html.py
```

Regenerates:
- `giro_2026/html/index.html`
- `giro_2026/html/stages/SXX.html`
- `giro_2026/html/riders/BXXX.html`

## Data Layout

- Stage links: `giro_2026/stage_links/SXX.json`
- Rider GPX: `giro_2026/courses/SXX/`
- Flight CSV: `giro_2026/flights/SXX/`
- Rider raws: `giro_2026/raw/riders/`
- Stage raws: `giro_2026/raw/stages/`
- HTML output: `giro_2026/html/`

## Notes

- Project uses Strava website session cookie (`_strava4_session`) in `strava_session_cookie.txt`.
- Rider withdrawal support in `giro_2026/riders.json`:
  - `withdraw_stage: -1` => rider still active
  - `withdraw_stage: N` => rider considered withdrawn after stage `SNN` (excluded from `S(N+1)+` updates/downloads)
- Heavy/generated local artifacts are not committed.
- If a rider has `activity_url` but GPX download fails, check `giro_2026/courses/SXX/_download_failures.txt`.
