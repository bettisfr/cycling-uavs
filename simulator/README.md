# Experiments

This directory contains the trace-driven optimization layer built on top of the
Giro 2026 dataset already collected in this repository.

The legacy scripts in the repository root are left untouched. Preprocessing,
instance generation, optimization, and visualization code live in importable
Python modules under `simulator/`. The only runnable entrypoint is
`simulator.main`.

## Build a Normalized Stage Trace

```bash
/home/fra/pyvenv/bin/python -m simulator.main --stage-id S18 --preprocess --only-preprocess
```

The entrypoint reads `giro_2026/stage_links/SXX.json`, keeps usable rider GPX
traces, and writes both CSV and Parquet outputs:

```text
simulator/output/traces/S18_rider_points.csv
simulator/output/traces/S18_rider_points.parquet
simulator/output/traces/S18_summary.json
```

The simulator derives deterministic weighted clusters without modifying these
rider traces. It first restricts each stage to its official race window. For
each 30-second bucket, riders connected within 80 meters of route progress form
one cluster. Clusters are ordered by progress along the reference road and
assigned proxy broadcast roles and weights: frontmost and main group `1.0`,
clusters between them `0.1`, and trailing clusters `0.05`.

```text
simulator/output/clusters/S18_30s_r80m_clusters.parquet
simulator/output/clusters/S18_30s_r80m_clusters_summary.json
```

All algorithms consume this shared cluster artifact. It is generated
automatically when missing or rebuilt with `--preprocess-clusters`.

Default filters:

- `status == found_public`
- `locked == true`
- `gpx_start_hhmm != "00:00"`
- `gpx_start_hhmm >= "06:00"`
- GPX file exists

The normalized trace is the first preprocessing artifact used to build
time-aligned rider traces, cyclist clusters, and routing instances.

Official start and finish windows are recorded in `simulator/stage_windows.json`.
The schedule uses CEST for every stage, including the opening stages held in
Bulgaria. Stage S10 records its first and last individual starts, but is excluded
from group-based experiments because its staggered starts require dedicated
preprocessing and editorial roles.

## Legacy Small MILP Instance

```bash
/home/fra/pyvenv/bin/python -m simulator.main --stage-id S18 --algorithm alg0
```

The initial MILP model is retained as a legacy smoke-test optimizer and is not
used by the current paper model. It reads the normalized
Parquet trace, restricts it to the official race window, aggregates rider
positions into fixed time buckets, and consumes the shared route-aware cluster
artifact. Static UAV waypoints are sampled every 250 meters along the reference
route; start, finish, and recharging stations are included and duplicates are
removed. The model maximizes weighted cluster coverage subject to waypoint
mobility, per-UAV movement budget, battery, recharging, and 10% safe-return
reserve constraints.
The current defaults use 30-second time slots, 6 UAVs, and the baseline station
layout, which places stations approximately every 7.5 km, including the start
and finish. Alternative layouts are `dense` (5 km) and `sparse` (10 km).
UAVs start fully charged at station waypoints unless `--free-start` is used.
The default performance profile uses a maximum UAV speed of 120 km/h and a
10 MJ battery. Battery values are expressed in joules. Each UAV
uses 15 kJ per 30-second airborne slot plus 150 J per meter traveled. Charging
profiles refill an empty battery in 15 (`fast`), 20 (`baseline`), or 25 (`slow`)
minutes. A charging UAV remains landed, consumes no flight energy, and provides
no coverage. The legacy optimizer remains waypoint-discretized.

The official road geometry is selected automatically from a clean GPX sample for
the stage, or can be selected by sample bib with `--reference-gpx`. The selected
rider is not treated as the cyclist to cover; its GPX only identifies the road.

## Run an Experiment

```bash
/home/fra/pyvenv/bin/python -m simulator.main \
  --stage-id S18 \
  --algorithm alg1 \
  --reference-gpx B047 \
  --render-map
```

Algorithms:

- `alg0`: exact MILP model.
- `alg1`: deterministic spatial-partition baseline. The road is split into one
  segment per UAV; each UAV prepositions, covers only its assigned segment, then
  stops covering and recharges as needed to reach the common finish.
- `alg2`: dual spatial-partition baseline. The road is split into `n/2`
  segments; each segment is assigned two UAVs, one tracking the frontmost group
  and one tracking the main group.

`alg1` and `alg2` use continuous ground-projected positions rather than route
waypoints. A UAV flies directly toward its current target at the configured
maximum speed; a long transfer spans multiple slots and may cut across bends in
the road. Route samples are used only to estimate race progress and segment
boundaries. The feasibility checker validates per-slot speed, battery evolution,
charging, reserve energy, common deployment, and terminal recovery.

The main entrypoint loads the normalized stage trace, builds the routing
instance, runs the selected algorithm, writes a solution JSON, and optionally
renders the Folium map.
If the normalized trace is missing, or if `--preprocess` is passed, the main
entrypoint first rebuilds the preprocessing artifacts.
By default each stage/algorithm/time-step combination overwrites a stable JSON
path. The stable HTML filename also includes the fleet size; use `--tag` only
when a separate run artifact is useful.

## Render a Solution Map

```bash
/home/fra/pyvenv/bin/python -m simulator.main \
  --stage-id S18 \
  --algorithm alg1 \
  --render-map
```

The map is written under `simulator/output/` and shows the reference route,
charging stations, and UAV positions with a time slider.
