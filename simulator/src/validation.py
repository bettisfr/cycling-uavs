"""Feasibility checks for generated UAV trajectories."""

from __future__ import annotations

import argparse
from collections import defaultdict

from simulator.src.algorithms import Point, Waypoint, distance_m
from simulator.src.partition import transfer_energy


ENERGY_TOLERANCE_J = 1e-3
DISTANCE_TOLERANCE_M = 1.0


def check_feasible(args: argparse.Namespace, result: dict) -> bool:
    """Return whether a continuous-position partition solution is feasible."""
    station_rows = result.get("stations", [])
    buckets = result.get("time_buckets", [])
    placements = result.get("placements", [])
    num_uavs = result.get("num_uavs")
    if (
        len(station_rows) < 2
        or not buckets
        or not isinstance(num_uavs, int)
        or num_uavs <= 0
    ):
        return False
    if len(placements) != num_uavs * len(buckets):
        return False

    stations = [
        Waypoint(row["lat"], row["lon"], "station", row["label"])
        for row in station_rows
    ]
    start = stations[0]
    finish = stations[-1]
    max_step_m = args.max_speed_mps * args.time_step_sec
    reserve_j = args.safety_reserve_fraction * args.battery_capacity

    by_uav: dict[int, list[dict]] = defaultdict(list)
    for placement in placements:
        uav = placement.get("uav")
        if not isinstance(uav, int) or not 0 <= uav < num_uavs:
            return False
        by_uav[uav].append(placement)

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

        previous = Point(start.lat, start.lon)
        battery = args.initial_battery
        for index, placement in enumerate(trajectory):
            current = Point(placement["lat"], placement["lon"])
            movement_m = distance_m(previous, current)
            if movement_m > max_step_m + DISTANCE_TOLERANCE_M:
                return False

            station_index = next(
                (
                    station_index
                    for station_index, station in enumerate(stations)
                    if distance_m(current, station) <= DISTANCE_TOLERANCE_M
                ),
                None,
            )
            is_recharging = placement.get("recharging") is True
            is_landed = placement.get("landed") is True
            completed = station_index == len(stations) - 1 and is_landed
            if is_recharging:
                if (
                    index == 0
                    or not is_landed
                    or station_index is None
                    or station_index == len(stations) - 1
                    or movement_m > DISTANCE_TOLERANCE_M
                ):
                    return False
                expected_battery = min(
                    args.battery_capacity,
                    battery + args.recharge_per_step,
                )
            elif is_landed:
                if index == 0 or station_index is None or movement_m > DISTANCE_TOLERANCE_M:
                    return False
                expected_battery = battery
            elif index == 0:
                expected_battery = battery
            else:
                spent = (
                    args.airborne_energy_per_step
                    + args.move_energy_per_meter * movement_m
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

            if not completed:
                return_energy = transfer_energy(
                    args,
                    min(distance_m(current, station) for station in stations),
                )
                if actual_battery + ENERGY_TOLERANCE_J < reserve_j + return_energy:
                    return False

            previous = current
            battery = actual_battery

    return True
