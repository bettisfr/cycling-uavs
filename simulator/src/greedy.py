"""Battery-aware greedy target-chasing baseline."""

from __future__ import annotations

import argparse
import math

from simulator.src.model import Cluster, Point, Station, distance_m
from simulator.src.partition import (
    POSITION_TOLERANCE_M,
    choose_leg_target,
    energy_cost,
    post_race_horizon_slots,
    station_at,
    step_toward,
    transfer_energy,
)


def nearest_station_target(
    args: argparse.Namespace,
    current: Point,
    battery: float,
    stations: list[Station],
    reserve_j: float,
) -> tuple[Point, int]:
    """Return the nearest station that remains safely reachable."""
    current_station = station_at(current, stations)
    if current_station is not None:
        station = stations[current_station]
        return Point(station.lat, station.lon), current_station

    reachable = [
        (index, station)
        for index, station in enumerate(stations)
        if transfer_energy(args, distance_m(current, station)) + reserve_j <= battery
    ]
    if not reachable:
        raise RuntimeError("greedy baseline stranded a UAV away from a station")
    index, station = min(reachable, key=lambda item: distance_m(current, item[1]))
    return Point(station.lat, station.lon), index


def role_families(cluster: Cluster) -> tuple[str, ...]:
    if cluster.role == "frontmost_main_group":
        return ("frontmost", "main")
    if cluster.role == "frontmost_group":
        return ("frontmost",)
    if cluster.role == "main_group":
        return ("main",)
    return ()


def choose_target(
    current: Point,
    future_clusters: list[list[Cluster]],
    max_step_m: float,
    coverage_radius_m: float,
    role_spacing_slots: int,
    assigned_roles: list[tuple[str, int]],
) -> tuple[int, int, Cluster] | None:
    """Choose a reachable future target, reserving short-term role duplicates."""
    candidates: list[tuple[int, int, Cluster]] = []
    for lead, clusters in enumerate(future_clusters):
        for index, cluster in enumerate(clusters):
            distance = distance_m(current, cluster)
            required_distance = max(0.0, distance - coverage_radius_m)
            if required_distance > max_step_m * (lead + 1):
                continue
            families = role_families(cluster)
            if any(
                assigned_family in families and abs(assigned_lead - lead) < role_spacing_slots
                for assigned_family, assigned_lead in assigned_roles
            ):
                continue
            candidates.append((lead, index, cluster))

    if not candidates:
        return None
    lead, index, cluster = min(
        candidates,
        key=lambda item: (
            -item[2].weight * (0.98 ** item[0]),
            distance_m(current, item[2]),
            item[0],
            -item[2].route_progress_m,
            item[1],
        ),
    )
    assigned_roles.extend((family, lead) for family in role_families(cluster))
    return lead, index, cluster


def solve_greedy_coverage(args: argparse.Namespace, instance: dict) -> dict:
    """Greedily assign each available UAV to one high-value cluster per slot."""
    stations: list[Station] = instance["stations"]
    start = Point(stations[0].lat, stations[0].lon)
    finish = Point(stations[-1].lat, stations[-1].lon)
    race_clusters: list[list[Cluster]] = instance["clusters"]
    race_riders: list[list[dict]] = instance["rider_points"]
    race_buckets: list[int] = instance["buckets"]
    race_slots = len(race_buckets)
    reserve_j = args.safety_reserve_fraction * args.battery_capacity
    max_step_m = args.max_speed_mps * args.time_step_sec
    lookahead_slots = max(
        1,
        math.ceil(args.greedy_lookahead_minutes * 60 / args.time_step_sec),
    )
    role_spacing_slots = max(
        1,
        math.ceil(args.greedy_role_spacing_minutes * 60 / args.time_step_sec),
    )

    positions = [start for _ in range(args.num_uavs)]
    batteries = [args.initial_battery for _ in range(args.num_uavs)]
    placements: list[dict] = []
    all_buckets = list(race_buckets)
    all_clusters = list(race_clusters)
    all_riders = list(race_riders)
    objective = 0.0
    total_weight = 0.0

    max_post_race_slots = post_race_horizon_slots(args, stations)
    t = 0
    while t < race_slots or not all(
        distance_m(position, finish) <= POSITION_TOLERANCE_M
        for position in positions
    ):
        if t >= race_slots + max_post_race_slots:
            raise RuntimeError("greedy baseline did not finish within the post-race horizon")
        if t >= len(all_buckets):
            all_buckets.append(all_buckets[-1] + 1)
            all_clusters.append([])
            all_riders.append([])

        clusters = all_clusters[t]
        assigned_roles: list[tuple[str, int]] = []
        chosen_positions: list[Point] = []
        target_active: list[bool] = []
        target_labels: list[str] = []
        target_offsets: list[int | None] = []
        target_stations: list[int | None] = []

        for d in range(args.num_uavs):
            current = positions[d]
            battery = batteries[d]
            if t == 0:
                chosen_positions.append(current)
                target_active.append(False)
                target_labels.append("deployment")
                target_offsets.append(None)
                target_stations.append(0)
                continue

            if t >= race_slots:
                current_station = station_at(current, stations)
                if (
                    current_station is not None
                    and current_station != len(stations) - 1
                    and battery < args.battery_capacity
                ):
                    station = stations[current_station]
                    leg_target = Point(station.lat, station.lon)
                    target_station = current_station
                else:
                    leg_target, target_station = choose_leg_target(
                        args,
                        current,
                        battery,
                        finish,
                        stations,
                        reserve_j,
                    )
                target_active.append(False)
                target_labels.append("terminal recovery")
                target_offsets.append(None)
                target_stations.append(target_station)
            else:
                target = choose_target(
                    current,
                    all_clusters[t : min(race_slots, t + lookahead_slots + 1)],
                    max_step_m,
                    args.coverage_radius_m,
                    role_spacing_slots,
                    assigned_roles,
                )
                if target is None:
                    leg_target, target_station = nearest_station_target(
                        args,
                        current,
                        battery,
                        stations,
                        reserve_j,
                    )
                    target_active.append(False)
                    target_labels.append("station")
                    target_offsets.append(None)
                    target_stations.append(target_station)
                else:
                    target_offset, cluster_index, cluster = target
                    leg_target, target_station = choose_leg_target(
                        args,
                        current,
                        battery,
                        Point(cluster.lat, cluster.lon),
                        stations,
                        reserve_j,
                    )
                    target_active.append(True)
                    target_labels.append(cluster.role or f"cluster_{cluster_index}")
                    target_offsets.append(target_offset)
                    target_stations.append(target_station)

            chosen = step_toward(current, leg_target, max_step_m)
            if energy_cost(args, current, chosen) > battery:
                raise RuntimeError(f"greedy baseline stranded UAV {d} at slot {t}")
            chosen_positions.append(chosen)

        airborne: list[bool] = []
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
            is_airborne = t > 0 and not stationary_at_station
            airborne.append(is_airborne)

            station_index = chosen_station
            if station_index is not None:
                kind = "station"
                label = stations[station_index].label
            elif target_active[d]:
                kind = "flight"
                label = target_labels[d]
            else:
                kind = "flight"
                label = "in_flight"
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
                    "covering": target_active[d] and is_airborne,
                    "target": target_labels[d],
                    "target_offset_slots": target_offsets[d],
                    "target_station": target_stations[d],
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
        "status_name": "GREEDY_COVERAGE_BASELINE",
        "objective": float(objective),
        "best_bound": None,
        "gap": None,
        "num_uavs": args.num_uavs,
        "time_step_sec": args.time_step_sec,
        "coverage_radius_m": args.coverage_radius_m,
        "time_buckets": all_buckets,
        "race_time_buckets": race_buckets,
        "post_race_slots": len(all_buckets) - race_slots,
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
