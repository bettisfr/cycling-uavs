"""Deterministic weighted-cluster preprocessing for simulator instances."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from scipy.spatial import cKDTree

from simulator.src.preprocessing import iter_gpx_points
from simulator.src.stages import official_window_utc


EARTH_RADIUS_M = 6_371_000.0
WEIGHT_POLICY = "editorial-v3-frontmost_1_main_1_intermediate_0.1_trailing_0.05"


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1 = math.radians(a[0])
    lat2 = math.radians(b[0])
    x = math.radians(b[1] - a[1]) * math.cos((lat1 + lat2) / 2.0)
    return EARTH_RADIUS_M * math.hypot(x, lat2 - lat1)


def route_samples(gpx_path: Path, spacing_m: float = 50.0) -> tuple[list[tuple[float, float]], list[float]]:
    raw = [(float(lat), float(lon)) for _time, lat, lon, _ele in iter_gpx_points(gpx_path)]
    if len(raw) < 2:
        raise RuntimeError(f"Reference route has too few points: {gpx_path}")
    samples = [raw[0]]
    progress = [0.0]
    cumulative = 0.0
    last_sample = 0.0
    for previous, current in zip(raw, raw[1:], strict=False):
        cumulative += distance_m(previous, current)
        if cumulative - last_sample >= spacing_m:
            samples.append(current)
            progress.append(cumulative)
            last_sample = cumulative
    if samples[-1] != raw[-1]:
        samples.append(raw[-1])
        progress.append(cumulative)
    return samples, progress


def bucketed_riders(
    trace_parquet: Path,
    time_step_sec: int,
    start_ts: int,
    finish_ts: int,
) -> dict[int, dict[str, tuple[float, float]]]:
    table = pq.read_table(trace_parquet, columns=["rider_id", "time_utc", "lat", "lon"])
    data = table.to_pydict()
    totals: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    for rider, time_utc, lat, lon in zip(
        data["rider_id"], data["time_utc"], data["lat"], data["lon"], strict=True
    ):
        time_ts = timestamp(time_utc)
        if not start_ts <= time_ts <= finish_ts:
            continue
        bucket = (time_ts - start_ts) // time_step_sec
        values = totals[(bucket, rider)]
        values[0] += lat
        values[1] += lon
        values[2] += 1.0
    result: dict[int, dict[str, tuple[float, float]]] = defaultdict(dict)
    for (bucket, rider), (lat, lon, count) in totals.items():
        result[bucket][rider] = (lat / count, lon / count)
    return dict(result)


def project_to_route(
    riders: dict[str, tuple[float, float]],
    route: list[tuple[float, float]],
    route_progress: list[float],
) -> dict[str, float]:
    mean_lat = math.radians(sum(lat for lat, _lon in route) / len(route))
    scale_x = EARTH_RADIUS_M * math.cos(mean_lat) * math.pi / 180.0
    scale_y = EARTH_RADIUS_M * math.pi / 180.0
    tree = cKDTree([(lon * scale_x, lat * scale_y) for lat, lon in route])
    ids = sorted(riders)
    _distances, indices = tree.query(
        [(riders[rider][1] * scale_x, riders[rider][0] * scale_y) for rider in ids]
    )
    return {
        rider: route_progress[int(route_idx)]
        for rider, route_idx in zip(ids, indices, strict=True)
    }


def connected_components(rider_progress: dict[str, float], radius_m: float) -> list[list[str]]:
    ids = sorted(rider_progress)
    parent = list(range(len(ids)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    for left in range(len(ids)):
        for right in range(left + 1, len(ids)):
            if abs(rider_progress[ids[left]] - rider_progress[ids[right]]) <= radius_m:
                union(left, right)
    groups: dict[int, list[str]] = defaultdict(list)
    for index, rider in enumerate(ids):
        groups[find(index)].append(rider)
    return [groups[root] for root in sorted(groups)]


def build_weighted_clusters(
    stage_id: str,
    trace_parquet: Path,
    route_gpx: Path,
    output_dir: Path,
    time_step_sec: int,
    cluster_radius_m: float,
    min_riders_per_bucket: int,
) -> dict:
    start_ts, finish_ts = official_window_utc(stage_id)
    riders_by_bucket = bucketed_riders(
        trace_parquet, time_step_sec, start_ts, finish_ts
    )
    route, route_progress = route_samples(route_gpx)
    rows: dict[str, list] = defaultdict(list)

    for bucket in sorted(riders_by_bucket):
        riders = riders_by_bucket[bucket]
        if len(riders) < min_riders_per_bucket:
            continue
        rider_progress = project_to_route(riders, route, route_progress)
        raw_clusters = []
        for members in connected_components(rider_progress, cluster_radius_m):
            lat = sum(riders[rider][0] for rider in members) / len(members)
            lon = sum(riders[rider][1] for rider in members) / len(members)
            raw_clusters.append(
                {
                    "lat": lat,
                    "lon": lon,
                    "rider_ids": members,
                    "rider_count": len(members),
                    "route_progress_m": sum(rider_progress[rider] for rider in members)
                    / len(members),
                }
            )
        raw_clusters.sort(
            key=lambda cluster: (
                -cluster["route_progress_m"],
                -cluster["rider_count"],
                cluster["lat"],
                cluster["lon"],
                cluster["rider_ids"],
            )
        )
        main_idx = min(
            range(len(raw_clusters)),
            key=lambda idx: (-raw_clusters[idx]["rider_count"], idx),
        )
        for cluster_id, cluster in enumerate(raw_clusters):
            if cluster_id == 0 and main_idx == 0:
                role = "frontmost_main_group"
                weight = 1.0
            elif cluster_id == 0:
                role = "frontmost_group"
                weight = 1.0
            elif cluster_id == main_idx:
                role = "main_group"
                weight = 1.0
            elif cluster_id < main_idx:
                role = "intermediate"
                weight = 0.1
            else:
                role = "trailing"
                weight = 0.05
            rows["stage_id"].append(stage_id)
            rows["bucket"].append(bucket)
            rows["cluster_id"].append(cluster_id)
            rows["lat"].append(cluster["lat"])
            rows["lon"].append(cluster["lon"])
            rows["rider_count"].append(cluster["rider_count"])
            rows["rider_ids"].append(cluster["rider_ids"])
            rows["route_progress_m"].append(cluster["route_progress_m"])
            rows["role"].append(role)
            rows["editorial_weight"].append(weight)

    radius_tag = f"{cluster_radius_m:g}".replace(".", "p")
    stem = f"{stage_id}_{time_step_sec}s_r{radius_tag}m_clusters"
    output_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = output_dir / f"{stem}.parquet"
    summary_path = output_dir / f"{stem}_summary.json"
    pq.write_table(pa.table(dict(rows)), parquet_path, compression="zstd")
    summary = {
        "stage_id": stage_id,
        "trace_parquet": str(trace_parquet),
        "route_gpx": str(route_gpx),
        "time_step_sec": time_step_sec,
        "cluster_radius_m": cluster_radius_m,
        "grouping_metric": "absolute_route_progress_difference",
        "race_window_start_ts": start_ts,
        "race_window_finish_ts": finish_ts,
        "min_riders_per_bucket": min_riders_per_bucket,
        "weight_policy": WEIGHT_POLICY,
        "buckets": len(set(rows["bucket"])),
        "clusters": len(rows["bucket"]),
        "parquet_path": str(parquet_path),
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary
