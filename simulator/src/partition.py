"""Build deterministic full-stage partition baselines for UAV coverage."""

from __future__ import annotations

import argparse
import math
from statistics import median

from simulator.src.algorithms import (
    Cluster,
    Point,
    Waypoint,
    choose_reference_gpx,
    distance_m,
    iter_gpx_points,
)


POSITION_TOLERANCE_M = 1.0


def step_toward(current: Point, target: Point, max_step_m: float) -> Point:
    """Advance in a straight line, reaching the target when it is one slot away."""
    distance = distance_m(current, target)
    if distance <= max_step_m:
        return Point(target.lat, target.lon)
    ratio = max_step_m / distance
    return Point(
        current.lat + ratio * (target.lat - current.lat),
        current.lon + ratio * (target.lon - current.lon),
    )


def energy_cost(args: argparse.Namespace, src: Point, dst: Point) -> float:
    movement_m = distance_m(src, dst)
    return args.airborne_energy_per_step + args.move_energy_per_meter * movement_m


def transfer_energy(args: argparse.Namespace, distance: float) -> float:
    """Energy for the minimum-slot direct flight over a given distance."""
    if distance <= POSITION_TOLERANCE_M:
        return 0.0
    max_step_m = args.max_speed_mps * args.time_step_sec
    slots = math.ceil(distance / max_step_m)
    return slots * args.airborne_energy_per_step + args.move_energy_per_meter * distance


def station_at(position: Point, stations: list[Waypoint]) -> int | None:
    for index, station in enumerate(stations):
        if distance_m(position, station) <= POSITION_TOLERANCE_M:
            return index
    return None


def route_and_group_progress(
    args: argparse.Namespace,
    instance: dict,
) -> tuple[list[Waypoint], list[float], list[Point], list[float]]:
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
    if distance_m(route[-1], final) > POSITION_TOLERANCE_M:
        route.append(Waypoint(final.lat, final.lon, "route", f"route_{len(route)}"))
        route_progress.append(cumulative_m)

    group_positions = []
    group_progress = []
    for points in instance["rider_points"]:
        group = Point(
            median(point["lat"] for point in points),
            median(point["lon"] for point in points),
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
    current: Point,
    battery: float,
    goal: Point,
    stations: list[Waypoint],
    reserve_j: float,
) -> tuple[Point, int | None]:
    goal_station = min(stations, key=lambda station: distance_m(goal, station))
    goal_is_station = station_at(goal, stations) is not None
    current_goal_m = distance_m(current, goal)
    required = transfer_energy(args, current_goal_m)
    if not goal_is_station:
        required += transfer_energy(args, distance_m(goal, goal_station))
    if current_goal_m <= POSITION_TOLERANCE_M and not goal_is_station:
        required += args.airborne_energy_per_step
    if required + reserve_j <= battery:
        return goal, None

    current_goal_distance = distance_m(current, goal)
    reachable = [
        (index, station)
        for index, station in enumerate(stations)
        if distance_m(current, station) > POSITION_TOLERANCE_M
        and transfer_energy(args, distance_m(current, station)) + reserve_j
        <= battery
    ]
    advancing = [
        (index, station)
        for index, station in reachable
        if distance_m(station, goal) < current_goal_distance
    ]
    if advancing:
        index, station = min(advancing, key=lambda item: distance_m(item[1], goal))
        return Point(station.lat, station.lon), index

    current_station = station_at(current, stations)
    if current_station is not None:
        station = stations[current_station]
        return Point(station.lat, station.lon), current_station
    if reachable:
        index, station = min(reachable, key=lambda item: distance_m(current, item[1]))
        return Point(station.lat, station.lon), index
    raise RuntimeError("partition baseline stranded a UAV away from a station")


def solve_partition(
    args: argparse.Namespace,
    instance: dict,
    drones_per_segment: int,
) -> dict:
    if args.num_uavs % drones_per_segment != 0:
        raise ValueError(
            f"The fleet size {args.num_uavs} is not divisible by "
            f"{drones_per_segment} drones per segment"
        )

    stations: list[Waypoint] = instance["stations"]
    start = Point(stations[0].lat, stations[0].lon)
    finish = Point(stations[-1].lat, stations[-1].lon)
    race_clusters: list[list[Cluster]] = instance["clusters"]
    race_riders: list[list[dict]] = instance["rider_points"]
    race_buckets: list[int] = instance["buckets"]
    route, route_progress, group_positions, group_progress = route_and_group_progress(
        args,
        instance,
    )
    race_slots = len(race_buckets)
    reserve_j = args.safety_reserve_fraction * args.battery_capacity
    max_step_m = args.max_speed_mps * args.time_step_sec
    num_segments = args.num_uavs // drones_per_segment

    route_length_m = route_progress[-1]
    segment_boundaries = [
        route_length_m * segment / num_segments
        for segment in range(num_segments + 1)
    ]
    starts_by_segment = [
        next(
            (t for t, progress in enumerate(group_progress) if progress >= boundary),
            race_slots - 1,
        )
        for boundary in segment_boundaries[:-1]
    ]
    ends_by_segment = starts_by_segment[1:] + [race_slots]
    ends_by_segment = [end - 1 for end in ends_by_segment]
    ends_by_segment[-1] = race_slots - 1
    uav_segments = [d // drones_per_segment for d in range(args.num_uavs)]
    segment_starts = [starts_by_segment[segment] for segment in uav_segments]
    segment_ends = [ends_by_segment[segment] for segment in uav_segments]
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
        min(range(len(stations)), key=lambda index: distance_m(stations[index], boundary))
        for boundary in boundary_points
    ]

    positions = [start for _ in range(args.num_uavs)]
    batteries = [args.initial_battery for _ in range(args.num_uavs)]
    committed_stations: list[int | None] = [None] * args.num_uavs
    placements: list[dict] = []
    all_buckets = list(race_buckets)
    all_clusters = list(race_clusters)
    all_riders = list(race_riders)
    objective = 0.0
    total_weight = 0.0

    max_post_race_slots = math.ceil(3600 / args.time_step_sec)
    t = 0
    while t < race_slots or not all(
        distance_m(position, finish) <= POSITION_TOLERANCE_M
        for position in positions
    ):
        if t >= race_slots + max_post_race_slots:
            raise RuntimeError("partition baseline did not finish within the post-race horizon")
        if t >= len(all_buckets):
            all_buckets.append(all_buckets[-1] + 1)
            all_clusters.append([])
            all_riders.append([])

        clusters = all_clusters[t]
        chosen_positions: list[Point] = []
        actively_tracking = [False] * args.num_uavs

        for d in range(args.num_uavs):
            current = positions[d]
            segment = uav_segments[d]
            start_slot = segment_starts[d]
            end_slot = segment_ends[d]

            if t == 0:
                chosen_positions.append(current)
                continue

            if t < start_slot:
                rendezvous = boundary_points[segment]
                staging_index = staging_stations[segment]
                staging = stations[staging_index]
                launch_slots = math.ceil(distance_m(staging, rendezvous) / max_step_m)
                goal: Point = (
                    Point(rendezvous.lat, rendezvous.lon)
                    if t >= start_slot - launch_slots
                    else Point(staging.lat, staging.lon)
                )
            elif t <= end_slot and t < race_slots:
                if drones_per_segment == 1:
                    goal = group_positions[t]
                else:
                    role_index = d % drones_per_segment
                    target = next(
                        (
                            cluster
                            for cluster in race_clusters[t]
                            if (
                                role_index == 0
                                and cluster.role
                                in {"frontmost_group", "frontmost_main_group"}
                            )
                            or (
                                role_index == 1
                                and cluster.role
                                in {"main_group", "frontmost_main_group"}
                            )
                        ),
                        None,
                    )
                    goal = target if target is not None else group_positions[t]
                actively_tracking[d] = True
            else:
                goal = finish

            committed = committed_stations[d]
            if committed is not None and distance_m(current, stations[committed]) <= POSITION_TOLERANCE_M:
                committed_stations[d] = None
                committed = None

            if committed is not None:
                station = stations[committed]
                leg_target = Point(station.lat, station.lon)
                target_station = committed
            else:
                leg_target, target_station = choose_leg_target(
                    args,
                    current,
                    batteries[d],
                    goal,
                    stations,
                    reserve_j,
                )
            if target_station is not None and distance_m(current, leg_target) > POSITION_TOLERANCE_M:
                committed_stations[d] = target_station
            if actively_tracking[d] and target_station is not None:
                actively_tracking[d] = False

            chosen = step_toward(current, leg_target, max_step_m)
            if energy_cost(args, current, chosen) > batteries[d]:
                raise RuntimeError(f"partition baseline stranded UAV {d} at slot {t}")
            chosen_positions.append(chosen)

        recharging = []
        airborne = []
        for d, chosen in enumerate(chosen_positions):
            current = positions[d]
            current_station = station_at(current, stations)
            chosen_station = station_at(chosen, stations)
            stationary_at_station = (
                t > 0
                and current_station is not None
                and current_station == chosen_station
                and distance_m(current, chosen) <= POSITION_TOLERANCE_M
            )
            completed = chosen_station == len(stations) - 1 and stationary_at_station
            is_recharging = (
                stationary_at_station
                and not completed
                and batteries[d] < args.battery_capacity
            )
            if is_recharging:
                batteries[d] = min(
                    args.battery_capacity,
                    batteries[d] + args.recharge_per_step,
                )
            elif t > 0 and not completed and not stationary_at_station:
                batteries[d] -= energy_cost(args, current, chosen)
            positions[d] = chosen
            recharging.append(is_recharging)
            is_airborne = t > 0 and not stationary_at_station
            airborne.append(is_airborne)
            kind = "station" if chosen_station is not None else "flight"
            label = (
                stations[chosen_station].label
                if chosen_station is not None
                else "in_flight"
            )
            placements.append(
                {
                    "uav": d,
                    "bucket": all_buckets[t],
                    "kind": kind,
                    "label": label,
                    "lat": chosen.lat,
                    "lon": chosen.lon,
                    "battery": batteries[d],
                    "landed": stationary_at_station,
                    "recharging": is_recharging,
                    "airborne": is_airborne,
                    "covering": actively_tracking[d] and is_airborne,
                    "segment": uav_segments[d],
                }
            )

        if t < race_slots:
            total_weight += sum(cluster.weight for cluster in clusters)
            objective += sum(
                cluster.weight
                for cluster in clusters
                if any(
                    airborne[d]
                    and distance_m(position, cluster) <= args.coverage_radius_m
                    for d, position in enumerate(positions)
                )
            )
        t += 1

    return {
        "status": 2,
        "status_name": (
            "PARTITION_BASELINE"
            if drones_per_segment == 1
            else "DUAL_PARTITION_BASELINE"
        ),
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
                    "rider_count": cluster.rider_count,
                    "role": cluster.role,
                    "route_progress_m": cluster.route_progress_m,
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
        "safety_reserve_fraction": args.safety_reserve_fraction,
        "airborne_energy_per_step": args.airborne_energy_per_step,
        "move_energy_per_meter": args.move_energy_per_meter,
        "max_speed_mps": args.max_speed_mps,
        "motion_model": "continuous_positions_straight_line_multi_slot",
        "placements": placements,
    }


def solve_greedy(args: argparse.Namespace, instance: dict) -> dict:
    return solve_partition(args, instance, drones_per_segment=1)


def solve_dual_partition(args: argparse.Namespace, instance: dict) -> dict:
    return solve_partition(args, instance, drones_per_segment=2)
