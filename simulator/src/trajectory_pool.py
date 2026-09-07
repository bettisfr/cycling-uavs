"""Rule-based candidate generation and marginal-greedy fleet selection."""

from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
import math

from simulator.src.model import Cluster, Point, Station, distance_m
from simulator.src.partition import (
    POSITION_TOLERANCE_M,
    energy_cost,
    post_race_horizon_slots,
    solve_partition,
    station_at,
    step_toward,
    transfer_energy,
)
from simulator.src.validation import check_feasible


TARGET_POLICIES = (
    "frontmost",
    "main",
    "highest_weight",
    "alternating_2m",
    "alternating_5m",
    "alternating_10m",
)
RECHARGE_THRESHOLDS = (0.15, 0.30, 0.45, 0.60, 0.75, 0.90)
STATION_POLICIES = ("nearest", "advance")
PREPOSITION_SEGMENT_COUNTS = (2, 3, 4, 6)


@dataclass(frozen=True)
class CandidatePolicy:
    target_policy: str
    recharge_threshold: float
    station_policy: str

    @property
    def identifier(self) -> str:
        threshold = int(round(self.recharge_threshold * 100))
        return f"{self.target_policy}_b{threshold}_{self.station_policy}"


@dataclass
class CandidateTrajectory:
    policy: CandidatePolicy
    placements: list[dict]
    covered_pairs: set[tuple[int, int]]
    label: str | None = None

    @property
    def identifier(self) -> str:
        return self.label or self.policy.identifier


def candidate_policies() -> list[CandidatePolicy]:
    return [
        CandidatePolicy(target_policy, recharge_threshold, station_policy)
        for target_policy in TARGET_POLICIES
        for recharge_threshold in RECHARGE_THRESHOLDS
        for station_policy in STATION_POLICIES
    ]


def role_target(clusters: list[Cluster], role: str) -> Cluster | None:
    return next((cluster for cluster in clusters if cluster.role == role), None)


def choose_target(
    policy: CandidatePolicy,
    clusters: list[Cluster],
    slot: int,
    time_step_sec: int,
) -> Cluster | None:
    if not clusters:
        return None

    frontmost = role_target(clusters, "frontmost_group") or role_target(
        clusters, "frontmost_main_group"
    )
    main = role_target(clusters, "main_group") or role_target(
        clusters, "frontmost_main_group"
    )
    if policy.target_policy == "frontmost":
        return frontmost or main
    if policy.target_policy == "main":
        return main or frontmost
    if policy.target_policy.startswith("alternating_"):
        minutes = int(policy.target_policy.removeprefix("alternating_").removesuffix("m"))
        epoch = slot // max(1, math.ceil(minutes * 60 / time_step_sec))
        preferred = frontmost if epoch % 2 == 0 else main
        return preferred or main or frontmost
    return min(
        clusters,
        key=lambda cluster: (-cluster.weight, -cluster.route_progress_m),
    )


def can_reach_target(
    args: argparse.Namespace,
    current: Point,
    battery: float,
    target: Point,
    stations: list[Station],
    reserve_j: float,
) -> bool:
    return_energy = min(
        transfer_energy(args, distance_m(target, station)) for station in stations
    )
    required = transfer_energy(args, distance_m(current, target)) + reserve_j + return_energy
    return required <= battery


def choose_station(
    args: argparse.Namespace,
    current: Point,
    battery: float,
    goal: Point,
    stations: list[Station],
    reserve_j: float,
    policy: str,
) -> tuple[Point, int]:
    reachable = [
        (index, station)
        for index, station in enumerate(stations)
        if transfer_energy(args, distance_m(current, station)) + reserve_j <= battery
    ]
    if not reachable:
        raise RuntimeError("candidate trajectory stranded away from a station")

    if policy == "advance":
        current_goal_distance = distance_m(current, goal)
        advancing = [
            item
            for item in reachable
            if distance_m(item[1], goal) < current_goal_distance
        ]
        if advancing:
            index, station = min(advancing, key=lambda item: distance_m(item[1], goal))
            return Point(station.lat, station.lon), index

    index, station = min(reachable, key=lambda item: distance_m(current, item[1]))
    return Point(station.lat, station.lon), index


def simulate_candidate(
    args: argparse.Namespace,
    instance: dict,
    policy: CandidatePolicy,
) -> CandidateTrajectory | None:
    stations: list[Station] = instance["stations"]
    start = Point(stations[0].lat, stations[0].lon)
    finish = Point(stations[-1].lat, stations[-1].lon)
    race_clusters: list[list[Cluster]] = instance["clusters"]
    race_buckets: list[int] = instance["buckets"]
    race_slots = len(race_buckets)
    reserve_j = args.safety_reserve_fraction * args.battery_capacity
    max_step_m = args.max_speed_mps * args.time_step_sec
    max_post_race_slots = post_race_horizon_slots(args, stations)
    all_buckets = list(race_buckets)
    all_clusters = list(race_clusters) + [[] for _ in range(max_post_race_slots)]
    for _ in range(max_post_race_slots):
        all_buckets.append(all_buckets[-1] + 1)

    position = start
    battery = args.initial_battery
    placements: list[dict] = []
    covered_pairs: set[tuple[int, int]] = set()

    for t, bucket in enumerate(all_buckets):
        current_station = station_at(position, stations)
        target: Cluster | None = None
        target_station: int | None = None
        target_label = "terminal recovery" if t >= race_slots else "station"

        if t == 0:
            chosen = position
        elif t >= race_slots:
            if distance_m(position, finish) <= POSITION_TOLERANCE_M:
                chosen = finish
                target_station = len(stations) - 1
            elif current_station is not None and battery < args.battery_capacity:
                station = stations[current_station]
                chosen = Point(station.lat, station.lon)
                target_station = current_station
            elif can_reach_target(args, position, battery, finish, stations, reserve_j):
                chosen = step_toward(position, finish, max_step_m)
                target_label = "terminal recovery"
            else:
                station_target, target_station = choose_station(
                    args,
                    position,
                    battery,
                    finish,
                    stations,
                    reserve_j,
                    policy.station_policy,
                )
                chosen = step_toward(position, station_target, max_step_m)
        else:
            target = choose_target(policy, all_clusters[t], t, args.time_step_sec)
            threshold_j = policy.recharge_threshold * args.battery_capacity
            if current_station is not None and battery < threshold_j:
                station = stations[current_station]
                chosen = Point(station.lat, station.lon)
                target_station = current_station
            elif target is not None and battery >= threshold_j and can_reach_target(
                args,
                position,
                battery,
                Point(target.lat, target.lon),
                stations,
                reserve_j,
            ):
                chosen = step_toward(position, Point(target.lat, target.lon), max_step_m)
                target_label = target.role or "highest_weight"
            else:
                goal = Point(target.lat, target.lon) if target is not None else finish
                station_target, target_station = choose_station(
                    args,
                    position,
                    battery,
                    goal,
                    stations,
                    reserve_j,
                    policy.station_policy,
                )
                chosen = step_toward(position, station_target, max_step_m)

        if t > 0 and energy_cost(args, position, chosen) > battery:
            return None

        chosen_station = station_at(chosen, stations)
        stationary_at_station = (
            t > 0
            and current_station is not None
            and current_station == chosen_station
            and distance_m(position, chosen) <= POSITION_TOLERANCE_M
        )
        completed = chosen_station == len(stations) - 1 and stationary_at_station
        is_recharging = (
            stationary_at_station
            and not completed
            and battery < args.battery_capacity
        )
        if is_recharging:
            battery = min(args.battery_capacity, battery + args.recharge_per_step)
        elif t > 0 and not completed and not stationary_at_station:
            battery -= energy_cost(args, position, chosen)

        is_airborne = t > 0 and not stationary_at_station
        if not completed:
            return_energy = min(
                transfer_energy(args, distance_m(chosen, station)) for station in stations
            )
            if battery + 1e-3 < reserve_j + return_energy:
                return None

        if t < race_slots:
            for cluster_index, cluster in enumerate(all_clusters[t]):
                if is_airborne and distance_m(chosen, cluster) <= args.coverage_radius_m:
                    covered_pairs.add((t, cluster_index))

        if chosen_station is not None:
            kind = "station"
            label = stations[chosen_station].label
        else:
            kind = "flight"
            label = target_label
        placements.append(
            {
                "uav": 0,
                "bucket": bucket,
                "kind": kind,
                "label": label,
                "lat": chosen.lat,
                "lon": chosen.lon,
                "battery": battery,
                "landed": stationary_at_station,
                "recharging": is_recharging,
                "airborne": is_airborne,
                "covering": target is not None and is_airborne,
                "candidate_policy": policy.identifier,
            }
        )
        position = chosen

    if distance_m(position, finish) > POSITION_TOLERANCE_M:
        return None
    return CandidateTrajectory(policy, placements, covered_pairs)


def partition_seed_candidates(
    args: argparse.Namespace,
    instance: dict,
    all_buckets: list[int],
) -> list[CandidateTrajectory]:
    """Generate prepositioned role-window missions as additional pool seeds."""
    race_slots = len(instance["clusters"])
    candidates: list[CandidateTrajectory] = []
    for num_segments in PREPOSITION_SEGMENT_COUNTS:
        partition_args = copy.copy(args)
        partition_args.num_uavs = 2 * num_segments
        partition = solve_partition(partition_args, instance, drones_per_segment=2)
        by_uav = {
            uav: [row for row in partition["placements"] if row["uav"] == uav]
            for uav in range(partition_args.num_uavs)
        }
        for uav, rows in by_uav.items():
            if not rows or len(rows) > len(all_buckets):
                continue
            last = rows[-1]
            finish = partition["stations"][-1]
            if distance_m(Point(last["lat"], last["lon"]), Station(**finish)) > POSITION_TOLERANCE_M:
                continue

            placements = [{**row, "uav": 0} for row in rows]
            for bucket in all_buckets[len(placements) :]:
                placements.append(
                    {
                        "uav": 0,
                        "bucket": bucket,
                        "kind": "station",
                        "label": finish["label"],
                        "lat": finish["lat"],
                        "lon": finish["lon"],
                        "battery": last["battery"],
                        "landed": True,
                        "recharging": False,
                        "airborne": False,
                        "covering": False,
                        "candidate_policy": "partition_seed",
                    }
                )

            covered_pairs: set[tuple[int, int]] = set()
            for t, row in enumerate(placements[:race_slots]):
                if not row["airborne"]:
                    continue
                position = Point(row["lat"], row["lon"])
                for cluster_index, cluster in enumerate(instance["clusters"][t]):
                    if distance_m(position, cluster) <= args.coverage_radius_m:
                        covered_pairs.add((t, cluster_index))

            role = "frontmost" if uav % 2 == 0 else "main"
            segment = rows[0].get("segment", uav // 2)
            policy = CandidatePolicy("partition_seed", 1.0, "advance")
            candidates.append(
                CandidateTrajectory(
                    policy,
                    placements,
                    covered_pairs,
                    label=f"partition_m{num_segments}_seg{segment}_{role}",
                )
            )
    return candidates


def select_candidates(
    candidates: list[CandidateTrajectory],
    weights: dict[tuple[int, int], float],
    fleet_size: int,
) -> list[CandidateTrajectory]:
    selected: list[CandidateTrajectory] = []
    covered: set[tuple[int, int]] = set()
    remaining = list(candidates)
    while remaining and len(selected) < fleet_size:
        candidate = max(
            remaining,
            key=lambda item: (
                sum(weights[pair] for pair in item.covered_pairs - covered),
                item.identifier,
            ),
        )
        gain = sum(weights[pair] for pair in candidate.covered_pairs - covered)
        if gain <= 0.0:
            break
        selected.append(candidate)
        covered.update(candidate.covered_pairs)
        remaining.remove(candidate)

    if not selected:
        raise RuntimeError("candidate pool contains no reward-contributing mission")
    while len(selected) < fleet_size:
        selected.append(selected[0])
    return selected


def filter_feasible_candidates(
    args: argparse.Namespace,
    stations: list[Station],
    buckets: list[int],
    candidates: list[CandidateTrajectory],
) -> list[CandidateTrajectory]:
    """Retain only single-UAV missions accepted by the shared validator."""
    station_rows = [
        {"label": station.label, "lat": station.lat, "lon": station.lon}
        for station in stations
    ]
    feasible: list[CandidateTrajectory] = []
    for candidate in candidates:
        result = {
            "stations": station_rows,
            "time_buckets": buckets,
            "placements": candidate.placements,
            "num_uavs": 1,
        }
        if check_feasible(args, result):
            feasible.append(candidate)
    return feasible


def solve_trajectory_pool(args: argparse.Namespace, instance: dict) -> dict:
    """Generate deterministic single-UAV missions and greedily select a fleet."""
    candidates: list[CandidateTrajectory] = []
    for policy in candidate_policies():
        try:
            candidate = simulate_candidate(args, instance, policy)
        except RuntimeError:
            candidate = None
        if candidate is not None:
            candidates.append(candidate)
    max_post_race_slots = post_race_horizon_slots(args, instance["stations"])
    all_buckets = list(instance["buckets"])
    for _ in range(max_post_race_slots):
        all_buckets.append(all_buckets[-1] + 1)
    candidates.extend(partition_seed_candidates(args, instance, all_buckets))
    candidates = filter_feasible_candidates(
        args,
        instance["stations"],
        all_buckets,
        candidates,
    )
    if not candidates:
        raise RuntimeError("no feasible trajectory was generated for the candidate pool")

    race_clusters: list[list[Cluster]] = instance["clusters"]
    weights = {
        (t, cluster_index): cluster.weight
        for t, clusters in enumerate(race_clusters)
        for cluster_index, cluster in enumerate(clusters)
    }
    selected = select_candidates(candidates, weights, args.num_uavs)
    covered_pairs = set().union(*(candidate.covered_pairs for candidate in selected))

    placements = [
        {**placement, "uav": uav}
        for uav, candidate in enumerate(selected)
        for placement in candidate.placements
    ]
    all_buckets = [placement["bucket"] for placement in selected[0].placements]
    all_clusters = race_clusters + [[] for _ in range(len(all_buckets) - len(race_clusters))]
    all_riders = instance["rider_points"] + [[] for _ in range(len(all_buckets) - len(race_clusters))]

    return {
        "status": 2,
        "status_name": "TRAJECTORY_POOL_GREEDY",
        "objective": float(sum(weights[pair] for pair in covered_pairs)),
        "best_bound": None,
        "gap": None,
        "num_uavs": args.num_uavs,
        "time_step_sec": args.time_step_sec,
        "coverage_radius_m": args.coverage_radius_m,
        "time_buckets": all_buckets,
        "race_time_buckets": instance["buckets"],
        "post_race_slots": len(all_buckets) - len(race_clusters),
        "clusters_per_bucket": [len(clusters) for clusters in all_clusters],
        "clusters": [
            [
                {
                    "bucket": all_buckets[t],
                    "cluster": cluster_index,
                    "lat": cluster.lat,
                    "lon": cluster.lon,
                    "weight": cluster.weight,
                    "rider_count": cluster.rider_count,
                    "role": cluster.role,
                    "route_progress_m": cluster.route_progress_m,
                }
                for cluster_index, cluster in enumerate(clusters)
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
        "total_cluster_weight": float(sum(weights.values())),
        "stations": [
            {"label": station.label, "lat": station.lat, "lon": station.lon}
            for station in instance["stations"]
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
        "candidate_pool_size": len(candidates),
        "candidate_policies": [candidate.identifier for candidate in candidates],
        "selected_policies": [candidate.identifier for candidate in selected],
        "placements": placements,
    }
