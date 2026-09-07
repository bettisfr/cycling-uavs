"""Macro-action dynamic programming with residual-coverage fleet selection."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
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
from simulator.src.trajectory_pool import CandidatePolicy, choose_target, solve_trajectory_pool


POLICIES = (
    "frontmost",
    "main",
    "highest_weight",
    "alternating_2m",
    "alternating_5m",
    "alternating_10m",
)
THRESHOLDS = (0.25, 0.55)


def _advance_uav(
    args: argparse.Namespace,
    stations: list[Station],
    position: Point,
    battery: float,
    goal: Cluster | None,
    recharge_threshold: float,
) -> tuple[Point, float, bool, bool, str] | None:
    reserve = args.safety_reserve_fraction * args.battery_capacity
    current_station = station_at(position, stations)
    if current_station == len(stations) - 1:
        return position, battery, False, False, stations[current_station].label
    if current_station is not None and battery < recharge_threshold * args.battery_capacity:
        return position, min(args.battery_capacity, battery + args.recharge_per_step), True, False, stations[current_station].label
    target = Point(goal.lat, goal.lon) if goal is not None else Point(stations[-1].lat, stations[-1].lon)
    try:
        leg_target, _ = choose_leg_target(args, position, battery, target, stations, reserve)
    except RuntimeError:
        return None
    chosen = step_toward(position, leg_target, args.max_speed_mps * args.time_step_sec)
    chosen_station = station_at(chosen, stations)
    stationary = current_station is not None and current_station == chosen_station and distance_m(position, chosen) <= POSITION_TOLERANCE_M
    if stationary:
        return chosen, min(args.battery_capacity, battery + args.recharge_per_step), True, False, stations[current_station].label
    spent = energy_cost(args, position, chosen)
    if spent > battery:
        return None
    new_battery = battery - spent
    safe_return = min(transfer_energy(args, distance_m(chosen, station)) for station in stations)
    if new_battery + 1e-3 < reserve + safe_return:
        return None
    label = stations[chosen_station].label if chosen_station is not None else "in_flight"
    return chosen, new_battery, False, True, label


def _recover_fleet(
    args: argparse.Namespace,
    stations: list[Station],
    positions: tuple[Point, ...],
    batteries: tuple[float, ...],
    last_bucket: int,
) -> tuple[list[int], list[tuple[dict, ...]]] | None:
    finish = Point(stations[-1].lat, stations[-1].lon)
    positions, batteries = list(positions), list(batteries)
    buckets: list[int] = []
    steps: list[tuple[dict, ...]] = []
    for offset in range(1, post_race_horizon_slots(args, stations) + 1):
        if all(distance_m(position, finish) <= POSITION_TOLERANCE_M for position in positions):
            break
        rows: list[dict] = []
        for uav, (position, battery) in enumerate(zip(positions, batteries, strict=True)):
            at_finish = distance_m(position, finish) <= POSITION_TOLERANCE_M
            if at_finish:
                chosen, new_battery, recharging, airborne, label = position, battery, False, False, stations[-1].label
            else:
                advanced = _advance_uav(args, stations, position, battery, None, 1.0)
                if advanced is None:
                    return None
                chosen, new_battery, recharging, airborne, label = advanced
            positions[uav], batteries[uav] = chosen, new_battery
            rows.append({"uav": uav, "bucket": last_bucket + offset, "kind": "flight" if airborne else "station", "label": label, "lat": chosen.lat, "lon": chosen.lon, "battery": new_battery, "landed": recharging or at_finish, "recharging": recharging, "airborne": airborne, "covering": False})
        buckets.append(last_bucket + offset)
        steps.append(tuple(rows))
    if not all(distance_m(position, finish) <= POSITION_TOLERANCE_M for position in positions):
        return None
    return buckets, steps


@dataclass(frozen=True)
class Label:
    position: Point
    battery: float
    value: float
    pairs: frozenset[tuple[int, int]]
    parent: "Label | None"
    rows: tuple[dict, ...]


def _state_key(args: argparse.Namespace, label: Label) -> tuple[int, int, int]:
    grid_m = args.coverage_radius_m
    lat = round(label.position.lat * 111_000.0 / grid_m)
    lon_scale = max(1.0, 111_000.0 * math.cos(math.radians(label.position.lat)))
    lon = round(label.position.lon * lon_scale / grid_m)
    battery = round(label.battery / args.dp_battery_bin_j)
    return lat, lon, battery


def _label_rows(label: Label) -> list[dict]:
    blocks: list[tuple[dict, ...]] = []
    while label.parent is not None:
        blocks.append(label.rows)
        label = label.parent
    return [row for block in reversed(blocks) for row in block]


def _simulate_action(
    args: argparse.Namespace,
    instance: dict,
    label: Label,
    start_slot: int,
    end_slot: int,
    policy_name: str,
    threshold: float,
    residual: set[tuple[int, int]],
    weights: dict[tuple[int, int], float],
) -> Label | None:
    stations: list[Station] = instance["stations"]
    position = label.position
    battery = label.battery
    pairs = set(label.pairs)
    value = label.value
    rows: list[dict] = []
    policy = CandidatePolicy(policy_name, threshold, "advance")
    for slot in range(start_slot, end_slot):
        clusters: list[Cluster] = instance["clusters"][slot]
        target = choose_target(policy, clusters, slot, args.time_step_sec)
        advanced = _advance_uav(args, stations, position, battery, target, threshold)
        if advanced is None:
            return None
        chosen, battery, recharging, airborne, station_label = advanced
        if airborne:
            for cluster_index, cluster in enumerate(clusters):
                pair = (slot, cluster_index)
                if pair in residual and distance_m(chosen, cluster) <= args.coverage_radius_m:
                    pairs.add(pair)
                    value += weights[pair]
        rows.append(
            {
                "uav": 0,
                "bucket": instance["buckets"][slot],
                "kind": "flight" if airborne else "station",
                "label": station_label,
                "lat": chosen.lat,
                "lon": chosen.lon,
                "battery": battery,
                "landed": recharging or (
                    not airborne and station_at(chosen, stations) == len(stations) - 1
                ),
                "recharging": recharging,
                "airborne": airborne,
                "covering": airborne and target is not None,
            }
        )
        position = chosen
    return Label(position, battery, value, frozenset(pairs), label, tuple(rows))


def _best_single_uav(
    args: argparse.Namespace,
    instance: dict,
    residual: set[tuple[int, int]],
    weights: dict[tuple[int, int], float],
) -> Label | None:
    stations: list[Station] = instance["stations"]
    start = Point(stations[0].lat, stations[0].lon)
    initial_row = {
        "uav": 0,
        "bucket": instance["buckets"][0],
        "kind": "station",
        "label": stations[0].label,
        "lat": start.lat,
        "lon": start.lon,
        "battery": args.initial_battery,
        "landed": False,
        "recharging": False,
        "airborne": False,
        "covering": False,
    }
    root = Label(start, args.initial_battery, 0.0, frozenset(), None, ())
    labels = [Label(start, args.initial_battery, 0.0, frozenset(), root, (initial_row,))]
    slots = len(instance["clusters"])
    for block_start in range(1, slots, args.dp_block_slots):
        block_end = min(slots, block_start + args.dp_block_slots)
        best_by_state: dict[tuple[int, int, int], Label] = {}
        for label in labels:
            for policy in POLICIES:
                for threshold in THRESHOLDS:
                    successor = _simulate_action(
                        args, instance, label, block_start, block_end,
                        policy, threshold, residual, weights,
                    )
                    if successor is None:
                        continue
                    key = _state_key(args, successor)
                    previous = best_by_state.get(key)
                    if previous is None or successor.value > previous.value:
                        best_by_state[key] = successor
        labels = sorted(best_by_state.values(), key=lambda item: -item.value)[: args.dp_label_limit]
        if not labels:
            return None
    return max(labels, key=lambda item: item.value, default=None)


def solve_dynamic_programming_greedy(args: argparse.Namespace, instance: dict) -> dict:
    """Select complete UAV missions by repeated macro-action dynamic programs."""
    incumbent = solve_trajectory_pool(args, instance)
    weights = {
        (slot, cluster_index): cluster.weight
        for slot, clusters in enumerate(instance["clusters"])
        for cluster_index, cluster in enumerate(clusters)
    }
    residual = set(weights)
    selected: list[Label] = []
    for _uav in range(args.num_uavs):
        label = _best_single_uav(args, instance, residual, weights)
        if label is None or label.value <= 0.0:
            break
        selected.append(label)
        residual.difference_update(label.pairs)
    if len(selected) != args.num_uavs:
        incumbent["status_name"] = "DP_MACRO_GREEDY_FALLBACK"
        incumbent["dp_incumbent_objective"] = incumbent["objective"]
        return incumbent

    stations: list[Station] = instance["stations"]
    recovery_data = []
    for label in selected:
        recovery = _recover_fleet(
            args, stations, (label.position,), (label.battery,), instance["buckets"][-1]
        )
        if recovery is None:
            incumbent["status_name"] = "DP_MACRO_GREEDY_FALLBACK"
            incumbent["dp_incumbent_objective"] = incumbent["objective"]
            return incumbent
        recovery_data.append(recovery)
    post_slots = max(len(buckets) for buckets, _steps in recovery_data)
    all_buckets = list(instance["buckets"])
    all_buckets.extend(instance["buckets"][-1] + offset for offset in range(1, post_slots + 1))
    placements: list[dict] = []
    for uav, (label, (_recovery_buckets, recovery_steps)) in enumerate(zip(selected, recovery_data, strict=True)):
        rows = [{**row, "uav": uav} for row in _label_rows(label)]
        rows.extend({**row, "uav": uav} for step in recovery_steps for row in step)
        last = rows[-1]
        while len(rows) < len(all_buckets):
            rows.append(
                {
                    **last,
                    "bucket": all_buckets[len(rows)],
                    "landed": True,
                    "recharging": False,
                    "airborne": False,
                    "covering": False,
                }
            )
        placements.extend(rows)
    covered = set(weights).difference(residual)
    result = {
        **incumbent,
        "status_name": "DP_MACRO_GREEDY",
        "objective": float(sum(weights[pair] for pair in covered)),
        "time_buckets": all_buckets,
        "race_time_buckets": instance["buckets"],
        "post_race_slots": post_slots,
        "clusters_per_bucket": [len(clusters) for clusters in instance["clusters"]] + [0] * post_slots,
        "clusters": incumbent["clusters"][: len(instance["clusters"])] + [[] for _ in range(post_slots)],
        "rider_points": incumbent["rider_points"][: len(instance["clusters"])] + [[] for _ in range(post_slots)],
        "placements": placements,
        "dp_block_slots": args.dp_block_slots,
        "dp_label_limit": args.dp_label_limit,
        "dp_battery_bin_j": args.dp_battery_bin_j,
        "dp_incumbent_objective": incumbent["objective"],
    }
    if result["objective"] < incumbent["objective"]:
        incumbent["status_name"] = "DP_MACRO_GREEDY_FALLBACK"
        incumbent["dp_incumbent_objective"] = incumbent["objective"]
        return incumbent
    return result
