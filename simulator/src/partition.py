"""Build a deterministic partition baseline for full-stage UAV coverage."""

from __future__ import annotations

import argparse
import math
from statistics import median

from simulator.src.algorithms import (
    Cluster,
    Waypoint,
    choose_reference_gpx,
    distance_m,
    iter_gpx_points,
)


def energy_cost(args: argparse.Namespace, src: Waypoint, dst: Waypoint) -> float:
    movement_m = distance_m(src, dst)
    if movement_m <= 1.0:
        return args.hover_energy_per_step
    return args.move_energy_per_meter * movement_m


def same_position(a: Waypoint, b: Waypoint, tolerance_m: float = 1.0) -> bool:
    return distance_m(a, b) <= tolerance_m


def nearest_station(stations: list[Waypoint], point: Waypoint | Cluster) -> Waypoint:
    return min(stations, key=lambda station: distance_m(station, point))


def step_toward(src: Waypoint, dst: Waypoint, max_distance_m: float, label: str) -> Waypoint:
    distance = distance_m(src, dst)
    if distance <= max_distance_m:
        return dst
    fraction = 0.999 * max_distance_m / distance
    return Waypoint(
        src.lat + fraction * (dst.lat - src.lat),
        src.lon + fraction * (dst.lon - src.lon),
        "transit",
        label,
    )


def route_and_group_progress(
    args: argparse.Namespace,
    instance: dict,
) -> tuple[list[Waypoint], list[float], list[Waypoint], list[float]]:
    gpx_path, _rider_id = choose_reference_gpx(args)
    raw_points = list(iter_gpx_points(gpx_path))
    if len(raw_points) < 2:
        raise RuntimeError(f"Reference route has too few points: {gpx_path}")

    route = [Waypoint(raw_points[0].lat, raw_points[0].lon, "route", "route_0")]
    route_progress = [0.0]
    cumulative_m = 0.0
    last_sample_m = 0.0
    for previous, current in zip(raw_points, raw_points[1:], strict=False):
        cumulative_m += distance_m(previous, current)
        if cumulative_m - last_sample_m >= 100.0:
            route.append(
                Waypoint(current.lat, current.lon, "route", f"route_{len(route)}")
            )
            route_progress.append(cumulative_m)
            last_sample_m = cumulative_m
    final = raw_points[-1]
    if distance_m(route[-1], final) > 1.0:
        route.append(Waypoint(final.lat, final.lon, "route", f"route_{len(route)}"))
        route_progress.append(cumulative_m)

    group_positions = []
    group_progress = []
    for t, points in enumerate(instance["rider_points"]):
        group = Waypoint(
            median(point["lat"] for point in points),
            median(point["lon"] for point in points),
            "race_group",
            f"race_group_{t}",
        )
        nearest_idx = min(
            range(len(route)),
            key=lambda idx: distance_m(group, route[idx]),
        )
        group_positions.append(group)
        group_progress.append(route_progress[nearest_idx])

    return route, route_progress, group_positions, group_progress


def choose_leg_target(
    args: argparse.Namespace,
    current: Waypoint,
    battery: float,
    goal: Waypoint,
    stations: list[Waypoint],
    reserve_j: float,
) -> Waypoint:
    goal_station = nearest_station(stations, goal)
    energy_via_goal = args.move_energy_per_meter * (
        distance_m(current, goal) + distance_m(goal, goal_station)
    )
    if goal.kind == "station":
        energy_via_goal = args.move_energy_per_meter * distance_m(current, goal)
    if energy_via_goal + reserve_j <= battery:
        return goal

    current_goal_distance = distance_m(current, goal)
    reachable = [
        station
        for station in stations
        if not same_position(current, station)
        and args.move_energy_per_meter * distance_m(current, station) + reserve_j
        <= battery
    ]
    advancing = [
        station
        for station in reachable
        if distance_m(station, goal) < current_goal_distance
    ]
    if advancing:
        return min(advancing, key=lambda station: distance_m(station, goal))
    if current.kind == "station":
        return current
    if reachable:
        return min(reachable, key=lambda station: distance_m(current, station))
    raise RuntimeError("alg1 partition baseline stranded a UAV away from a station")


def solve_greedy(args: argparse.Namespace, instance: dict) -> dict:
    stations: list[Waypoint] = instance["stations"]
    race_clusters: list[list[Cluster]] = instance["clusters"]
    race_riders: list[list[dict]] = instance["rider_points"]
    race_buckets: list[int] = instance["buckets"]
    route, route_progress, group_positions, group_progress = route_and_group_progress(
        args,
        instance,
    )
    race_slots = len(race_buckets)
    max_step_m = args.max_speed_mps * args.time_step_sec
    reserve_j = 0.1 * args.battery_capacity
    finish = stations[-1]

    route_length_m = route_progress[-1]
    segment_boundaries = [
        route_length_m * d / args.num_uavs
        for d in range(args.num_uavs + 1)
    ]
    segment_starts = [
        next(
            (t for t, progress in enumerate(group_progress) if progress >= boundary),
            race_slots - 1,
        )
        for boundary in segment_boundaries[:-1]
    ]
    segment_ends = segment_starts[1:] + [race_slots]
    segment_ends = [end - 1 for end in segment_ends]
    segment_ends[-1] = race_slots - 1
    boundary_points = [
        route[
            min(
                range(len(route)),
                key=lambda idx: abs(route_progress[idx] - boundary),
            )
        ]
        for boundary in segment_boundaries[:-1]
    ]
    staging_stations = [
        nearest_station(stations, boundary)
        for boundary in boundary_points
    ]

    positions = [stations[0] for _ in range(args.num_uavs)]
    batteries = [args.initial_battery for _ in range(args.num_uavs)]
    committed_targets: list[Waypoint | None] = [None] * args.num_uavs
    placements: list[dict] = []
    all_buckets = list(race_buckets)
    all_clusters = list(race_clusters)
    all_riders = list(race_riders)
    objective = 0
    total_weight = 0

    max_post_race_slots = 4 * race_slots
    t = 0
    while t < race_slots or not all(same_position(position, finish) for position in positions):
        if t >= race_slots + max_post_race_slots:
            raise RuntimeError("alg1 partition baseline did not finish within the post-race horizon")
        if t >= len(all_buckets):
            all_buckets.append(all_buckets[-1] + 1)
            all_clusters.append([])
            all_riders.append([])

        clusters = all_clusters[t]
        chosen_targets: list[Waypoint] = []
        covering = [False] * args.num_uavs

        for d in range(args.num_uavs):
            current = positions[d]
            start = segment_starts[d]
            end = segment_ends[d]

            if t == 0:
                chosen_targets.append(current)
                continue

            if t < start:
                rendezvous = boundary_points[d]
                staging = staging_stations[d]
                launch_slots = math.ceil(distance_m(staging, rendezvous) / max_step_m)
                goal = rendezvous if t >= start - launch_slots else staging
            elif t <= end and t < race_slots:
                goal = group_positions[t]
                covering[d] = True
            else:
                goal = finish

            committed = committed_targets[d]
            if committed is not None and same_position(current, committed):
                committed_targets[d] = None
                committed = None

            if covering[d]:
                leg_target = choose_leg_target(
                    args,
                    current,
                    batteries[d],
                    goal,
                    stations,
                    reserve_j,
                )
            elif committed is not None:
                leg_target = committed
            else:
                leg_target = choose_leg_target(
                    args,
                    current,
                    batteries[d],
                    goal,
                    stations,
                    reserve_j,
                )
                if leg_target.kind == "station" and not same_position(current, leg_target):
                    committed_targets[d] = leg_target

            chosen = step_toward(
                current,
                leg_target,
                max_step_m,
                f"transit_to_{leg_target.label}",
            )
            if (
                not (chosen.kind == "station" and same_position(current, chosen))
                and energy_cost(args, current, chosen) > batteries[d]
            ):
                raise RuntimeError(f"alg1 partition baseline stranded UAV {d} at slot {t}")
            if covering[d] and leg_target.kind == "station":
                covering[d] = False
            chosen_targets.append(chosen)

        recharging = []
        for d, chosen in enumerate(chosen_targets):
            landed = (
                t > 0
                and chosen.kind == "station"
                and same_position(positions[d], chosen)
            )
            is_recharging = landed and batteries[d] < args.battery_capacity
            if landed:
                if is_recharging:
                    batteries[d] = min(
                        args.battery_capacity,
                        batteries[d] + args.recharge_per_step,
                    )
            elif t > 0:
                batteries[d] -= energy_cost(args, positions[d], chosen)
            positions[d] = chosen
            recharging.append(is_recharging)
            placements.append(
                {
                    "uav": d,
                    "bucket": all_buckets[t],
                    "candidate": None,
                    "kind": chosen.kind,
                    "label": chosen.label,
                    "lat": chosen.lat,
                    "lon": chosen.lon,
                    "battery": batteries[d],
                    "landed": landed,
                    "recharging": is_recharging,
                    "covering": covering[d] and not landed,
                    "segment": d,
                }
            )

        if t < race_slots:
            total_weight += sum(cluster.weight for cluster in clusters)
            objective += sum(
                cluster.weight
                for cluster in clusters
                if any(
                    covering[d]
                    and not recharging[d]
                    and distance_m(position, cluster) <= args.coverage_radius_m
                    for d, position in enumerate(positions)
                )
            )
        t += 1

    return {
        "status": 2,
        "status_name": "PARTITION_BASELINE",
        "objective": float(objective),
        "best_bound": None,
        "gap": None,
        "num_uavs": args.num_uavs,
        "time_step_sec": args.time_step_sec,
        "coverage_radius_m": args.coverage_radius_m,
        "time_buckets": all_buckets,
        "race_time_buckets": race_buckets,
        "post_race_slots": len(all_buckets) - race_slots,
        "segment_starts": segment_starts,
        "segment_ends": segment_ends,
        "clusters_per_bucket": [len(clusters) for clusters in all_clusters],
        "clusters": [
            [
                {
                    "bucket": all_buckets[t],
                    "cluster": k,
                    "lat": cluster.lat,
                    "lon": cluster.lon,
                    "weight": cluster.weight,
                }
                for k, cluster in enumerate(clusters)
            ]
            for t, clusters in enumerate(all_clusters)
        ],
        "rider_points": [
            [
                {
                    "bucket": all_buckets[t],
                    "rider_id": point["rider_id"],
                    "lat": point["lat"],
                    "lon": point["lon"],
                }
                for point in points
            ]
            for t, points in enumerate(all_riders)
        ],
        "total_cluster_weight": total_weight,
        "stations": [
            {"label": station.label, "lat": station.lat, "lon": station.lon}
            for station in stations
        ],
        "station_metadata": instance["station_metadata"],
        "battery_capacity": args.battery_capacity,
        "initial_battery": args.initial_battery,
        "recharge_per_step": args.recharge_per_step,
        "placements": placements,
    }
