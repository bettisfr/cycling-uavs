# Cycling UAVs

No-API Strava + FlightAware pipeline, competition-config driven.

## Setup

```bash
cd /home/fra/Desktop/github/cycling-uavs
/home/fra/pyvenv/bin/pip install -r requirements.txt
```

Competition root example: `giro_2026/` (must contain `competition.json`).

## Daily 4-Step Pipeline

### 1) Collect raw rider pages + parse + download GPX into pool

```bash
/home/fra/pyvenv/bin/python -m pipeline.step1_collect_gpx_pool \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026
```

### 2) Assign stages from GPX pool (date-based, lock-safe)

```bash
/home/fra/pyvenv/bin/python -m pipeline.step2_assign_stages_from_pool \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026
```

### 3) Refresh outputs (maps + stage images + HTML)

```bash
/home/fra/pyvenv/bin/python -m pipeline.step3_refresh_outputs \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026
```

### 4) Lock confirmed activities

```bash
/home/fra/pyvenv/bin/python -m pipeline.step4_lock_confirmed_activities \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026
```

## Stage Raw Import (optional)

```bash
/home/fra/pyvenv/bin/python -m pipeline.import_stage_raw \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026 \
  --stage-id S12
```

Default raw file path: `raw/stages/S12.txt` inside competition dir.

## GPX Download (manual)

```bash
# all stages
/home/fra/pyvenv/bin/python -m pipeline.download_stage_gpx \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026 \
  --all

# one stage
/home/fra/pyvenv/bin/python -m pipeline.download_stage_gpx \
  --competition-dir /home/fra/Desktop/github/cycling-uavs/giro_2026 \
  --stage-id S12
```

## Flight CSV (single stage)

```bash
/home/fra/pyvenv/bin/python -m pipeline.tracker_export \
  "https://it.flightaware.com/live/flight/ASR251/history/..." \
  --stage-id S12
```

## Key Paths (inside competition dir)

- `riders.json`
- `stages.json`
- `stage_links/SXX.json`
- `gpx_store/*.gpx`
- `courses/SXX/*.gpx` (symlinks)
- `flights/SXX/*.csv`
- `raw/riders/BXXX.txt`
- `raw/stages/SXX.txt`
- `html/`

## Notes

- Session cookie file: `strava_session_cookie.txt` in repository root.
- Timezone, flight callsign, and stage rules are read from `competition.json`.
- No backward compatibility is maintained for old CLI flags (`--dataset-dir`, legacy raw paths).
