"""Solve a small discretized UAV coverage MILP from a normalized stage trace."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import gurobipy as gp
from gurobipy import GRB
import pyarrow.parquet as pq

from simulator.src.stages import official_window_utc


EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float


@dataclass(frozen=True)
class Waypoint:
    lat: float
    lon: float
    kind: str
    label: str


@dataclass(frozen=True)
class Cluster:
    lat: float
    lon: float
    weight: float
    rider_count: int = 0
    role: str = ""
    route_progress_m: float = 0.0


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_time_utc(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def distance_m(a: Point | Waypoint | Cluster, b: Point | Waypoint | Cluster) -> float:
    lat1 = math.radians(a.lat)
    lat2 = math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    mean_lat = (lat1 + lat2) / 2.0
    x = dlon * math.cos(mean_lat)
    return EARTH_RADIUS_M * math.hypot(x, dlat)


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
            Path("/home/fra/Desktop/github") / text.removeprefix("/mnt/data/github/")
        )
    elif text.startswith("/home/fra/Desktop/github/"):
        candidates.append(Path("/mnt/data/github") / text.removeprefix("/home/fra/Desktop/github/"))

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
        stage_dir = (
            repo_root() / "giro_2026" / "courses" / args.stage_id.upper()
        )
        matches = sorted(stage_dir.glob(f"{bib}__*.gpx"))
        if not matches:
            raise FileNotFoundError(
                f"No GPX found for bib {bib} in stage {args.stage_id.upper()}"
            )
        if len(matches) > 1:
            raise RuntimeError(
                f"Multiple GPX files found for bib {bib} in {stage_dir}"
            )
        return matches[0].resolve(), bib

    stage_path = repo_root() / "giro_2026" / "stage_links" / f"{args.stage_id.upper()}.json"
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

    usable.sort(key=lambda a: float(a.get("gpx_km") or 0.0), reverse=True)
    chosen = usable[0]
    return resolve_path(chosen["gpx_path"], repo_root()), chosen.get("rider_id")


def station_waypoints_from_gpx(args: argparse.Namespace) -> tuple[list[Waypoint], dict]:
    if args.num_stations <= 0:
        return [], {}

    gpx_path, rider_id = choose_reference_gpx(args)
    points = list(iter_gpx_points(gpx_path))
    if len(points) < 2:
        raise RuntimeError(f"Reference GPX has too few points: {gpx_path}")

    cumulative = [0.0]
    for prev, curr in zip(points, points[1:], strict=False):
        cumulative.append(cumulative[-1] + distance_m(prev, curr))

    total_m = cumulative[-1]
    station_spacing_m = getattr(args, "station_spacing_m", None)
    num_stations = (
        math.ceil(total_m / station_spacing_m) + 1
        if station_spacing_m
        else args.num_stations
    )
    stations: list[Waypoint] = []
    idx = 0
    for station_idx in range(num_stations):
        target = (
            station_idx * total_m / max(num_stations - 1, 1)
            if num_stations > 1
            else total_m / 2.0
        )
        while idx + 1 < len(cumulative) and cumulative[idx + 1] < target:
            idx += 1
        candidate_indices = [idx]
        if idx + 1 < len(points):
            candidate_indices.append(idx + 1)
        nearest_idx = min(candidate_indices, key=lambda i: abs(cumulative[i] - target))
        point = points[nearest_idx]
        stations.append(Waypoint(point.lat, point.lon, "station", f"station_{station_idx}"))

    return stations, {
        "reference_gpx": str(gpx_path),
        "reference_rider_id": rider_id,
        "reference_distance_m": total_m,
        "station_spacing_m": total_m / max(num_stations - 1, 1),
        "num_stations": num_stations,
    }


def route_waypoints_from_gpx(
    args: argparse.Namespace,
    stations: list[Waypoint],
) -> list[Waypoint]:
    gpx_path, _rider_id = choose_reference_gpx(args)
    points = list(iter_gpx_points(gpx_path))
    if len(points) < 2:
        raise RuntimeError(f"Reference GPX has too few points: {gpx_path}")

    selected = [points[0]]
    cumulative_m = 0.0
    last_selected_m = 0.0
    for previous, current in zip(points, points[1:], strict=False):
        cumulative_m += distance_m(previous, current)
        if cumulative_m - last_selected_m >= args.waypoint_spacing_m:
            selected.append(current)
            last_selected_m = cumulative_m
    if distance_m(selected[-1], points[-1]) > 1.0:
        selected.append(points[-1])

    waypoints = []
    for point in selected:
        if any(distance_m(point, station) <= 1.0 for station in stations):
            continue
        if any(distance_m(point, waypoint) <= 1.0 for waypoint in waypoints):
            continue
        waypoints.append(
            Waypoint(point.lat, point.lon, "route", f"route_{len(waypoints)}")
        )
    return waypoints


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

    acc: dict[tuple[int, str], list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
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
        key = (bucket, rider_id)
        acc[key][0] += lat
        acc[key][1] += lon
        acc[key][2] += 1.0

    by_bucket: dict[int, dict[str, Point]] = defaultdict(dict)
    for (bucket, rider_id), (lat_sum, lon_sum, count) in acc.items():
        by_bucket[bucket][rider_id] = Point(lat_sum / count, lon_sum / count)
    return dict(by_bucket)


def greedy_clusters(points: list[Point], radius_m: float) -> list[Cluster]:
    remaining = list(points)
    clusters: list[Cluster] = []

    while remaining:
        seed = remaining[0]
        members = [p for p in remaining if distance_m(seed, p) <= radius_m]
        member_ids = {id(p) for p in members}
        remaining = [p for p in remaining if id(p) not in member_ids]
        clusters.append(
            Cluster(
                lat=sum(p.lat for p in members) / len(members),
                lon=sum(p.lon for p in members) / len(members),
                weight=len(members),
            )
        )

    return clusters


def build_instance(args: argparse.Namespace) -> dict:
    by_bucket = read_bucketed_rider_positions(
        args.trace_parquet, args.time_step_sec, args.stage_id
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
    selected_buckets = [
        bucket
        for bucket in sorted(weighted_by_bucket)
    ][: args.max_time_buckets]
    if not selected_buckets:
        raise RuntimeError("No cluster time bucket is available for this stage.")

    clusters_by_t: list[list[Cluster]] = []
    rider_points_by_t: list[list[dict]] = []
    for bucket in selected_buckets:
        rider_points = [
            {"rider_id": rider_id, "lat": point.lat, "lon": point.lon}
            for rider_id, point in sorted(by_bucket[bucket].items())
        ]
        clusters_by_t.append(weighted_by_bucket[bucket])
        rider_points_by_t.append(rider_points)

    stations, station_metadata = station_waypoints_from_gpx(args)
    route_waypoints = route_waypoints_from_gpx(args, stations)
    candidates = stations + route_waypoints
    candidates_by_t = [candidates for _clusters in clusters_by_t]
    station_metadata.update(
        {
            "waypoint_spacing_m": args.waypoint_spacing_m,
            "num_route_waypoints": len(route_waypoints),
            "num_waypoints": len(candidates),
            "waypoint_policy": "static_route_sampling_with_station_deduplication",
        }
    )

    return {
        "buckets": selected_buckets,
        "clusters": clusters_by_t,
        "rider_points": rider_points_by_t,
        "candidates": candidates_by_t,
        "stations": stations,
        "station_metadata": station_metadata,
    }


def solve_instance(args: argparse.Namespace, instance: dict) -> dict:
    clusters_by_t: list[list[Cluster]] = instance["clusters"]
    rider_points_by_t: list[list[dict]] = instance["rider_points"]
    candidates_by_t: list[list[Point]] = instance["candidates"]
    time_count = len(clusters_by_t)
    drones = range(args.num_uavs)

    model = gp.Model("bike_race_uav_coverage")
    model.Params.OutputFlag = 1 if args.verbose else 0
    model.Params.TimeLimit = args.time_limit_sec

    x = {}
    for d in drones:
        for t, candidates in enumerate(candidates_by_t):
            for v in range(len(candidates)):
                x[d, t, v] = model.addVar(vtype=GRB.BINARY, name=f"x[{d},{t},{v}]")

    z = {}
    for t, clusters in enumerate(clusters_by_t):
        for k in range(len(clusters)):
            z[t, k] = model.addVar(vtype=GRB.BINARY, name=f"z[{t},{k}]")

    move = {}
    move_distance = {}
    max_step_m = args.max_speed_mps * args.time_step_sec
    for d in drones:
        for t in range(time_count - 1):
            for u, src in enumerate(candidates_by_t[t]):
                for v, dst in enumerate(candidates_by_t[t + 1]):
                    dist = distance_m(src, dst)
                    if dist <= max_step_m:
                        move[d, t, u, v] = model.addVar(
                            vtype=GRB.BINARY,
                            obj=0.0,
                            name=f"m[{d},{t},{u},{v}]",
                        )
                        move_distance[d, t, u, v] = dist

    battery = {}
    for d in drones:
        for t in range(time_count):
            battery[d, t] = model.addVar(
                lb=0.0,
                ub=args.battery_capacity,
                vtype=GRB.CONTINUOUS,
                name=f"b[{d},{t}]",
            )

    spill = {}
    for d in drones:
        for t in range(time_count - 1):
            spill[d, t] = model.addVar(
                lb=0.0,
                ub=args.recharge_per_step,
                vtype=GRB.CONTINUOUS,
                name=f"spill[{d},{t}]",
            )

    model.update()

    for d in drones:
        model.addConstr(
            battery[d, 0] == args.initial_battery,
            name=f"initial_battery[{d}]",
        )
        for t, candidates in enumerate(candidates_by_t):
            model.addConstr(
                gp.quicksum(x[d, t, v] for v in range(len(candidates))) == 1,
                name=f"one_position[{d},{t}]",
            )

    if not args.allow_same_waypoint:
        for t, candidates in enumerate(candidates_by_t):
            for v, candidate in enumerate(candidates):
                capacity = args.station_capacity if candidate.kind == "station" else 1
                model.addConstr(
                    gp.quicksum(x[d, t, v] for d in drones) <= capacity,
                    name=f"single_uav_per_waypoint[{t},{v}]",
                )

    if not args.free_start:
        for d in drones:
            station_vars = [
                x[d, 0, v]
                for v, candidate in enumerate(candidates_by_t[0])
                if candidate.kind == "station"
            ]
            model.addConstr(
                gp.quicksum(station_vars) == 1,
                name=f"start_at_station[{d}]",
            )

    for d in drones:
        for t in range(time_count - 1):
            for u in range(len(candidates_by_t[t])):
                outgoing = [
                    var
                    for (dd, tt, uu, _vv), var in move.items()
                    if dd == d and tt == t and uu == u
                ]
                model.addConstr(
                    gp.quicksum(outgoing) == x[d, t, u],
                    name=f"flow_out[{d},{t},{u}]",
                )
            for v in range(len(candidates_by_t[t + 1])):
                incoming = [
                    var
                    for (dd, tt, _uu, vv), var in move.items()
                    if dd == d and tt == t and vv == v
                ]
                model.addConstr(
                    gp.quicksum(incoming) == x[d, t + 1, v],
                    name=f"flow_in[{d},{t},{v}]",
                )

            recharge_terms = [
                var
                for (dd, tt, u, v), var in move.items()
                if dd == d and tt == t
                and candidates_by_t[t][u].kind == "station"
                and candidates_by_t[t + 1][v].kind == "station"
                and distance_m(candidates_by_t[t][u], candidates_by_t[t + 1][v]) <= 1.0
            ]
            recharge = gp.quicksum(recharge_terms)
            energy_terms = [
                (
                    0.0
                    if (
                        candidates_by_t[t][u].kind == "station"
                        and candidates_by_t[t + 1][v].kind == "station"
                        and move_distance[d, t, u, v] <= 1.0
                    )
                    else args.hover_energy_per_step
                    if move_distance[d, t, u, v] <= 1.0
                    else args.move_energy_per_meter * move_distance[d, t, u, v]
                )
                * var
                for (dd, tt, u, v), var in move.items()
                if dd == d and tt == t
            ]
            energy_spent = gp.quicksum(energy_terms)
            model.addConstr(
                battery[d, t] >= energy_spent,
                name=f"enough_battery[{d},{t}]",
            )
            model.addConstr(
                battery[d, t + 1]
                == battery[d, t]
                - energy_spent
                + args.recharge_per_step * recharge
                - spill[d, t],
                name=f"battery_update[{d},{t}]",
            )
            model.addConstr(
                spill[d, t] <= args.recharge_per_step * recharge,
                name=f"spill_only_at_station[{d},{t}]",
            )

    reserve_j = args.safety_reserve_fraction * args.battery_capacity
    for d in drones:
        for t, candidates in enumerate(candidates_by_t):
            return_energy = gp.quicksum(
                (
                    reserve_j
                    + args.move_energy_per_meter
                    * min(distance_m(candidate, station) for station in instance["stations"])
                )
                * x[d, t, v]
                for v, candidate in enumerate(candidates)
            )
            model.addConstr(
                battery[d, t] >= return_energy,
                name=f"safe_return[{d},{t}]",
            )

    for t, clusters in enumerate(clusters_by_t):
        for k, cluster in enumerate(clusters):
            covering_positions = []
            for d in drones:
                for v, candidate in enumerate(candidates_by_t[t]):
                    if distance_m(candidate, cluster) <= args.coverage_radius_m:
                        availability = x[d, t, v]
                        if t < time_count - 1 and candidate.kind == "station":
                            recharge_var = move.get((d, t, v, v))
                            if recharge_var is not None:
                                availability -= recharge_var
                        covering_positions.append(availability)
            model.addConstr(
                z[t, k] <= gp.quicksum(covering_positions),
                name=f"covered_if_reached[{t},{k}]",
            )

    if args.max_movement_m is not None:
        for d in drones:
            movement_terms = []
            for (dd, t, u, v), var in move.items():
                if dd != d:
                    continue
                dist = distance_m(candidates_by_t[t][u], candidates_by_t[t + 1][v])
                movement_terms.append(dist * var)
            model.addConstr(
                gp.quicksum(movement_terms) <= args.max_movement_m,
                name=f"movement_budget[{d}]",
            )

    model.setObjective(
        gp.quicksum(
            clusters_by_t[t][k].weight * z[t, k]
            for t, clusters in enumerate(clusters_by_t)
            for k in range(len(clusters))
        ),
        GRB.MAXIMIZE,
    )
    model.optimize()

    placements = []
    if model.SolCount:
        for d in drones:
            for t, candidates in enumerate(candidates_by_t):
                for v, candidate in enumerate(candidates):
                    if x[d, t, v].X > 0.5:
                        placements.append(
                            {
                                "uav": d,
                                "bucket": instance["buckets"][t],
                                "candidate": v,
                                "kind": candidate.kind,
                                "label": candidate.label,
                                "lat": candidate.lat,
                                "lon": candidate.lon,
                                "battery": battery[d, t].X,
                            }
                        )

    return {
        "status": model.Status,
        "status_name": status_name(model.Status),
        "objective": model.ObjVal if model.SolCount else None,
        "best_bound": model.ObjBound if model.SolCount else None,
        "gap": model.MIPGap if model.SolCount else None,
        "num_uavs": args.num_uavs,
        "time_step_sec": args.time_step_sec,
        "coverage_radius_m": args.coverage_radius_m,
        "time_buckets": instance["buckets"],
        "clusters_per_bucket": [len(c) for c in clusters_by_t],
        "clusters": [
            [
                {
                    "bucket": instance["buckets"][t],
                    "cluster": k,
                    "lat": cluster.lat,
                    "lon": cluster.lon,
                    "weight": cluster.weight,
                    "rider_count": cluster.rider_count,
                    "role": cluster.role,
                    "route_progress_m": cluster.route_progress_m,
                }
                for k, cluster in enumerate(clusters)
            ]
            for t, clusters in enumerate(clusters_by_t)
        ],
        "rider_points": [
            [
                {
                    "bucket": instance["buckets"][t],
                    "rider_id": point["rider_id"],
                    "lat": point["lat"],
                    "lon": point["lon"],
                }
                for point in points
            ]
            for t, points in enumerate(rider_points_by_t)
        ],
        "total_cluster_weight": sum(c.weight for clusters in clusters_by_t for c in clusters),
        "stations": [
            {"label": s.label, "lat": s.lat, "lon": s.lon}
            for s in instance["stations"]
        ],
        "station_metadata": instance["station_metadata"],
        "battery_capacity": args.battery_capacity,
        "initial_battery": args.initial_battery,
        "recharge_per_step": args.recharge_per_step,
        "safety_reserve_fraction": args.safety_reserve_fraction,
        "hover_energy_per_step": args.hover_energy_per_step,
        "move_energy_per_meter": args.move_energy_per_meter,
        "placements": placements,
    }


def status_name(status: int) -> str:
    names = {
        GRB.OPTIMAL: "OPTIMAL",
        GRB.INFEASIBLE: "INFEASIBLE",
        GRB.TIME_LIMIT: "TIME_LIMIT",
        GRB.INTERRUPTED: "INTERRUPTED",
        GRB.UNBOUNDED: "UNBOUNDED",
        GRB.INF_OR_UNBD: "INF_OR_UNBD",
    }
    return names.get(status, str(status))


# Expose alg1 beside the alg0 MILP through this module's public interface.
from simulator.src.partition import solve_dual_partition, solve_greedy  # noqa: E402


ALGORITHM_NAMES = {
    "alg0": "MILP",
    "alg1": "partition baseline",
    "alg2": "dual partition baseline",
}


def solve_algorithm(name: str, args: argparse.Namespace, instance: dict) -> dict:
    if name == "alg0":
        return solve_instance(args, instance)
    if name == "alg1":
        return solve_greedy(args, instance)
    if name == "alg2":
        return solve_dual_partition(args, instance)
    raise ValueError(f"Unsupported algorithm: {name}")
