# Giro 2026 Data Model

This folder stores manual research data for riders, Strava links, stage activities, and relay flight tracks.

## Files

- `stages.json`: stage metadata and per-stage flight tracking state.
- `teams.json`: team registry (`team_id`, team name, country).
- `riders.json`: minimal rider registry (`rider_id`, bib, name, nationality, team_id, team_name).
- `rider_links.json`: mapping from `rider_id` to Strava athlete profile and confidence.
- `stage_links/`: one file per stage (`S01.json` ... `S21.json`) with rider activity URLs and lookup status.

## Why this split

- Team data appears once in `teams.json`.
- Rider identity is minimal and stable in `riders.json`.
- Strava profile mapping is independent (`rider_links.json`).
- Stage-level activity URLs are split by day in `stage_links/`.

## Update workflow

1. Fill `stages.json` with all 21 stages.
2. Fill `teams.json` with all teams.
3. Fill `riders.json` with all riders (about 176, one entry per bib).
4. Resolve athlete profiles in `rider_links.json`.
5. For each stage, fill `stage_links/Sxx.json` with public activity URLs when available.
6. Update `stages.json.flight` fields for relay aircraft data and exported CSV path.

`tracker_export.py` can update catalog files automatically:
- `strava --stage-id Sxx --rider-id Bxxx` updates `stage_links/Sxx.json`
- `flightaware --stage-id Sxx` updates `stages.json` flight metadata

## Status semantics

- `rider_links.status`
  - `confirmed`: profile identity verified.
  - `candidate`: plausible profile, not fully verified.
  - `private`: profile found but not usable for stage extraction.
  - `not_found`: no reliable profile found.
  - `unknown`: not checked yet.

- `stage_links/Sxx.json -> activities[].status`
  - `found_public`: public activity URL available.
  - `private_or_missing`: no public URL for that stage.
  - `not_checked`: not inspected yet.

## Notes

- Keep `checked_at` updated on each manual verification.
- Use ISO timestamps with timezone, e.g. `2026-05-09T22:00:00+02:00`.
- Keep `notes` short and factual.
- Keep all repository text in English.
