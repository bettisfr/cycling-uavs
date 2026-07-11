"""Feasibility checks for generated UAV trajectories."""

from __future__ import annotations

import argparse
from collections import defaultdict

from experiments.algorithms.milp import Point, distance_m


ENERGY_TOLERANCE_J = 1e-3
DISTANCE_TOLERANCE_M = 1e-3


def check_feasible(args: argparse.Namespace, result: dict) -> bool:
    """Return whether an alg1 solution satisfies its operational constraints."""
    stations = result.get("stations", [])
    buckets = result.get("time_buckets", [])
    placements = result.get("placements", [])
    num_uavs = result.get("num_uavs")
    if not stations or not buckets or not isinstance(num_uavs, int) or num_uavs <= 0:
        return False
    if len(placements) != num_uavs * len(buckets):
        return False

    by_uav: dict[int, list[dict]] = defaultdict(list)
    for placement in placements:
        uav = placement.get("uav")
        if not isinstance(uav, int) or not 0 <= uav < num_uavs:
            return False
        by_uav[uav].append(placement)

    start = Point(stations[0]["lat"], stations[0]["lon"])
    finish = Point(stations[-1]["lat"], stations[-1]["lon"])
    station_points = {
        station["label"]: Point(station["lat"], station["lon"])
        for station in stations
    }
    max_step_m = args.max_speed_mps * args.time_step_sec

    for uav in range(num_uavs):
        trajectory = by_uav.get(uav, [])
        if len(trajectory) != len(buckets):
            return False
        if [placement.get("bucket") for placement in trajectory] != buckets:
            return False

        first = Point(trajectory[0]["lat"], trajectory[0]["lon"])
        last = Point(trajectory[-1]["lat"], trajectory[-1]["lon"])
        if distance_m(first, start) > DISTANCE_TOLERANCE_M:
            return False
        if distance_m(last, finish) > DISTANCE_TOLERANCE_M:
            return False

        previous = start
        battery = args.initial_battery
        for index, placement in enumerate(trajectory):
            current = Point(placement["lat"], placement["lon"])
            movement_m = distance_m(previous, current)
            if movement_m > max_step_m + DISTANCE_TOLERANCE_M:
                return False

            if placement.get("kind") == "station":
                station = station_points.get(placement.get("label"))
                if station is None or distance_m(current, station) > DISTANCE_TOLERANCE_M:
                    return False

            is_recharging = placement.get("recharging") is True
            is_landed = placement.get("landed") is True
            if is_recharging:
                if index == 0 or not is_landed or placement.get("kind") != "station":
                    return False
                if movement_m > DISTANCE_TOLERANCE_M:
                    return False
                expected_battery = min(
                    args.battery_capacity,
                    battery + args.recharge_per_step,
                )
            elif is_landed:
                if index == 0 or placement.get("kind") != "station":
                    return False
                if movement_m > DISTANCE_TOLERANCE_M:
                    return False
                expected_battery = battery
            elif index == 0:
                expected_battery = battery
            else:
                spent = (
                    args.hover_energy_per_step
                    if movement_m <= 1.0
                    else args.move_energy_per_meter * movement_m
                )
                if spent > battery + ENERGY_TOLERANCE_J:
                    return False
                expected_battery = battery - spent

            actual_battery = placement.get("battery")
            if not isinstance(actual_battery, (int, float)):
                return False
            if not -ENERGY_TOLERANCE_J <= actual_battery <= args.battery_capacity + ENERGY_TOLERANCE_J:
                return False
            if abs(actual_battery - expected_battery) > ENERGY_TOLERANCE_J:
                return False

            previous = current
            battery = actual_battery

    return True
