"""Build continuous-position routing instances from trace artifacts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import pyarrow.parquet as pq

from simulator.src.model import Cluster, Point, Station, distance_m
from simulator.src.stages import official_window_utc


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_time_utc(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def resolve_path(path_text: str, repo: Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        return (repo / path).resolve()

    text = str(path)
    candidates = [path]
    if text.startswith("/mnt/data/github/"):
        candidates.append(
            Path("/home/fra/Desktop/github")
            / text.removeprefix("/mnt/data/github/")
        )
    elif text.startswith("/home/fra/Desktop/github/"):
        candidates.append(
            Path("/mnt/data/github")
            / text.removeprefix("/home/fra/Desktop/github/")
        )

    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return path


def iter_gpx_points(gpx_path: Path):
    for _event, elem in ET.iterparse(gpx_path, events=("end",)):
        if local_name(elem.tag) != "trkpt":
            continue
        lat = elem.attrib.get("lat")
        lon = elem.attrib.get("lon")
        if lat and lon:
            yield Point(float(lat), float(lon))
        elem.clear()


def choose_reference_gpx(args: argparse.Namespace) -> tuple[Path, str | None]:
    if args.reference_gpx:
        reference = str(args.reference_gpx)
        explicit_path = resolve_path(reference, repo_root())
        if explicit_path.is_file():
            return explicit_path, None

        bib = reference.upper()
        if bib.isdigit():
            bib = f"B{int(bib):03d}"
        stage_dir = repo_root() / "giro_2026" / "courses" / args.stage_id.upper()
        matches = sorted(stage_dir.glob(f"{bib}__*.gpx"))
        if not matches:
            raise FileNotFoundError(
                f"No GPX found for bib {bib} in stage {args.stage_id.upper()}"
            )
        if len(matches) > 1:
            raise RuntimeError(f"Multiple GPX files found for bib {bib} in {stage_dir}")
        return matches[0].resolve(), bib

    stage_path = (
        repo_root()
        / "giro_2026"
        / "stage_links"
        / f"{args.stage_id.upper()}.json"
    )
    data = json.loads(stage_path.read_text(encoding="utf-8"))
    usable = [
        activity
        for activity in data.get("activities", [])
        if activity.get("status") == "found_public"
        and activity.get("locked")
        and activity.get("gpx_path")
        and activity.get("gpx_start_hhmm", "00:00") >= "06:00"
    ]
    if not usable:
        raise RuntimeError(f"No reference GPX candidate found in {stage_path}")

    usable.sort(
        key=lambda activity: float(activity.get("gpx_km") or 0.0),
        reverse=True,
    )
    chosen = usable[0]
    return resolve_path(chosen["gpx_path"], repo_root()), chosen.get("rider_id")


def stations_from_gpx(args: argparse.Namespace) -> tuple[list[Station], dict]:
    if args.station_spacing_m <= 0:
        raise ValueError("station spacing must be positive")

    gpx_path, rider_id = choose_reference_gpx(args)
    points = list(iter_gpx_points(gpx_path))
    if len(points) < 2:
        raise RuntimeError(f"Reference GPX has too few points: {gpx_path}")

    cumulative = [0.0]
    for previous, current in zip(points, points[1:], strict=False):
        cumulative.append(cumulative[-1] + distance_m(previous, current))

    total_m = cumulative[-1]
    num_stations = math.ceil(total_m / args.station_spacing_m) + 1
    stations: list[Station] = []
    index = 0
    for station_index in range(num_stations):
        target = station_index * total_m / (num_stations - 1)
        while index + 1 < len(cumulative) and cumulative[index + 1] < target:
            index += 1
        candidate_indices = [index]
        if index + 1 < len(points):
            candidate_indices.append(index + 1)
        nearest_index = min(
            candidate_indices,
            key=lambda candidate: abs(cumulative[candidate] - target),
        )
        point = points[nearest_index]
        stations.append(Station(point.lat, point.lon, f"station_{station_index}"))

    return stations, {
        "reference_gpx": str(gpx_path),
        "reference_rider_id": rider_id,
        "reference_distance_m": total_m,
        "station_spacing_m": total_m / (num_stations - 1),
        "num_stations": num_stations,
        "motion_model": "continuous_positions_straight_line_multi_slot",
    }


def read_bucketed_rider_positions(
    parquet_path: Path,
    time_step_sec: int,
    stage_id: str,
) -> dict[int, dict[str, Point]]:
    table = pq.read_table(
        parquet_path,
        columns=["rider_id", "time_utc", "lat", "lon"],
    )
    data = table.to_pydict()
    start_ts, finish_ts = official_window_utc(stage_id)

    accumulated: dict[tuple[int, str], list[float]] = defaultdict(
        lambda: [0.0, 0.0, 0.0]
    )
    for rider_id, time_utc, lat, lon in zip(
        data["rider_id"],
        data["time_utc"],
        data["lat"],
        data["lon"],
        strict=True,
    ):
        time_ts = parse_time_utc(time_utc)
        if not start_ts <= time_ts <= finish_ts:
            continue
        bucket = (time_ts - start_ts) // time_step_sec
        values = accumulated[(bucket, rider_id)]
        values[0] += lat
        values[1] += lon
        values[2] += 1.0

    by_bucket: dict[int, dict[str, Point]] = defaultdict(dict)
    for (bucket, rider_id), (lat_sum, lon_sum, count) in accumulated.items():
        by_bucket[bucket][rider_id] = Point(lat_sum / count, lon_sum / count)
    return dict(by_bucket)


def build_instance(args: argparse.Namespace) -> dict:
    by_bucket = read_bucketed_rider_positions(
        args.trace_parquet,
        args.time_step_sec,
        args.stage_id,
    )
    cluster_table = pq.read_table(args.cluster_parquet).to_pydict()
    weighted_by_bucket: dict[int, list[Cluster]] = defaultdict(list)
    for bucket, lat, lon, rider_count, role, weight, progress in zip(
        cluster_table["bucket"],
        cluster_table["lat"],
        cluster_table["lon"],
        cluster_table["rider_count"],
        cluster_table["role"],
        cluster_table["editorial_weight"],
        cluster_table["route_progress_m"],
        strict=True,
    ):
        weighted_by_bucket[bucket].append(
            Cluster(lat, lon, weight, rider_count, role, progress)
        )

    selected_buckets = sorted(weighted_by_bucket)[: args.max_time_buckets]
    if not selected_buckets:
        raise RuntimeError("No cluster time bucket is available for this stage.")

    clusters_by_t: list[list[Cluster]] = []
    rider_points_by_t: list[list[dict]] = []
    for bucket in selected_buckets:
        rider_points_by_t.append(
            [
                {"rider_id": rider_id, "lat": point.lat, "lon": point.lon}
                for rider_id, point in sorted(by_bucket[bucket].items())
            ]
        )
        clusters_by_t.append(weighted_by_bucket[bucket])

    stations, station_metadata = stations_from_gpx(args)
    return {
        "buckets": selected_buckets,
        "clusters": clusters_by_t,
        "rider_points": rider_points_by_t,
        "stations": stations,
        "station_metadata": station_metadata,
    }
