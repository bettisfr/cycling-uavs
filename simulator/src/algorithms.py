"""Algorithm registry for continuous-position routing policies."""

from __future__ import annotations

import argparse

from simulator.src.greedy import solve_greedy_coverage
from simulator.src.partition import solve_partition
from simulator.src.trajectory_pool import solve_trajectory_pool


ALGORITHM_NAMES = {
    "alg1": "trajectory-pool marginal greedy",
    "bs1": "fixed route-partition baseline",
    "bs2": "greedy target-chasing baseline",
}


def solve_algorithm(name: str, args: argparse.Namespace, instance: dict) -> dict:
    if name == "alg1":
        return solve_trajectory_pool(args, instance)
    if name == "bs1":
        return solve_partition(args, instance, args.drones_per_segment)
    if name == "bs2":
        return solve_greedy_coverage(args, instance)
    raise ValueError(f"Unsupported algorithm: {name}")
